import json
import sqlite3

from busboard import replay
from busboard.archive import EventLog, InputArchive
from busboard.compare import CompareStore, compare
from busboard.departures import Departure
from busboard.ucop import UcopArrival

from conftest import local, make_zip, trip_updates, vehicle_positions


def test_loop_visits_paired_in_order():
    """UCOP's data-key is the same for both visits of a looping trip; pair them by schedule order."""
    now = local(2026, 11, 6, 19, 0)

    def dep(seq, est):
        return Departure("20261106", "T_LOOP", "0:1117", seq, "2", "2", "", "Pulse", False, est, est, 2,
                         None, None, False, False, False)

    def arr(est):
        return UcopArrival("20261106", "T_LOOP", "0:1117", "2", "Pulse", est, est, "ok", "", "")

    ours = [dep(4, local(2026, 11, 6, 19, 50)), dep(2, local(2026, 11, 6, 19, 30))]
    ucop = [arr(local(2026, 11, 6, 19, 30)), arr(local(2026, 11, 6, 19, 50))]
    rows = compare(ours, ucop, now, tolerance_s=90, past_grace_s=60, window_ahead_s=7200)
    assert [(r.visit, r.stop_sequence, r.category) for r in rows] == [(0, 2, "match"), (1, 4, "match")]


def test_store_migrates_old_schema(tmp_path):
    db = tmp_path / "compare.db"
    con = sqlite3.connect(db)
    con.executescript("""
      CREATE TABLE snapshots (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, stop_code TEXT NOT NULL,
        ucop_count INTEGER, ours_count INTEGER, ucop_error TEXT);
      CREATE TABLE rows (snapshot_id INTEGER NOT NULL, ts INTEGER NOT NULL, stop_code TEXT NOT NULL,
        service_date TEXT, trip_id TEXT, route TEXT, category TEXT, our_sched INTEGER, our_est INTEGER,
        our_src INTEGER, stale_vehicle INTEGER, stale_block INTEGER, ucop_sched INTEGER, ucop_est INTEGER,
        ucop_status TEXT, diff_s INTEGER, our_headsign TEXT, ucop_headsign TEXT, headsign_differs INTEGER);
      INSERT INTO snapshots (ts, stop_code) VALUES (1, '1117');
    """)
    con.commit()
    con.close()
    store = CompareStore(db)
    now = local(2026, 11, 6, 18, 0)
    rows = compare([], [UcopArrival("20261106", "X", "0:1117", "4", "Pulse", now + 60, now + 60, "ok", "On Time",
                                    "Arriving in 1 min")],
                   now, tolerance_s=90, past_grace_s=60, window_ahead_s=3600)
    store.record("1117", now, rows, 1, 0, None, "archive/20261106/180000")
    snap, latest = store.latest("1117")
    assert snap["archive"] == "archive/20261106/180000"
    assert latest[0]["ucop_countdown"] == "Arriving in 1 min" and latest[0]["category"] == "ucop_only"


def test_event_log(tmp_path):
    log = EventLog(tmp_path)
    log.write("static_index", trips_kept=10, trips_added=2)
    (path,) = (tmp_path / "logs").glob("events-*.jsonl")
    rec = json.loads(path.read_text())
    assert rec["event"] == "static_index" and rec["trips_kept"] == 10


UCOP_HTML = """<html><body><div class="arrivals"><!--arrivals:start-->
<a class="arrival" href="#" data-key="20261106:T_STOP:0:1117"><h3 class="arrival-route">
<span class="arrival-route-name">4</span><span class="arrival-headsign">to Falmouth Shaw&#39;s</span></h3>
<p class="arrival-times"><span class="arrival-time-superseded">6:05 pm</span><span class="arrival-arrow"></span>
<span>6:08 pm</span><span class="arrival-status" data-status="late">3 min late</span></p></a>
<a class="arrival" href="#" data-key="20261106:T_ORIGIN:0:1117"><h3 class="arrival-route">
<span class="arrival-route-name">2</span><span class="arrival-headsign">to Prides Church</span></h3>
<p class="arrival-times"><span class="arrival-time-superseded">6:32 pm</span><span class="arrival-arrow"></span>
<span>6:45 pm</span><span class="arrival-status" data-status="late">13 min late</span></p></a>
<!--arrivals:end--></div></body></html>"""


def test_replay_end_to_end(tmp_path, capsys):
    data = tmp_path / "data"
    archive = InputArchive(data)
    zip_path = tmp_path / "gtfs.zip"
    zip_path.write_bytes(make_zip())
    now = local(2026, 11, 6, 17, 55)
    name = archive.save_static(zip_path, '"etag-1"', now - 3600, keep=5)
    # A second save of the same version is a no-op
    assert archive.save_static(zip_path, '"etag-1"', now, keep=5) == name
    tu = trip_updates({"T_STOP": {2: local(2026, 11, 6, 18, 8)}, "T_ORIGIN": {1: local(2026, 11, 6, 18, 34)}})
    vp = vehicle_positions({"T_STOP": ("2645", now - 20)})
    archive.save_snapshot(now, {"ts": now, "static": name, "static_version": '"etag-1"', "stops": ["1117"],
                                "ucop": {"1117": {"fetched_at": now, "error": None}}},
                          {"TripUpdate.pb": tu, "VehiclePosition.pb": vp, "ucop-1117.html": UCOP_HTML.encode()})

    rows_path = tmp_path / "rows.jsonl"
    assert replay.main([str(data), "--rows", str(rows_path), "--mismatches"]) == 0
    rows = {r["trip_id"]: r for r in map(json.loads, rows_path.read_text().splitlines())}
    assert rows["T_STOP"]["category"] == "match" and rows["T_STOP"]["vehicle_age_s"] == 20
    # We estimate 6:36 from the first stop; UCOP says 6:45
    assert rows["T_ORIGIN"]["category"] == "time_diff" and rows["T_ORIGIN"]["origin_delay_s"] == 240
    out = capsys.readouterr().out
    assert "1 snapshots replayed" in out and "T_ORIGIN" in out


def test_trips_ending_here_only_in_ours_are_not_counted():
    now = local(2026, 11, 6, 18, 0)
    d = Departure("20261106", "T_ENDS", "0:1117", 2, "4", "4", "", "Pulse", True, now + 600, now + 600, 2,
                  None, None, False, False, False)
    later = UcopArrival("20261106", "OTHER", "0:1117", "4", "Pulse", now + 3600, now + 3600, "ok", "", "")
    rows = {r.trip_id: r for r in compare([d], [later], now, tolerance_s=90, past_grace_s=60, window_ahead_s=7200)}
    assert rows["T_ENDS"].category == "ours_terminating" and rows["T_ENDS"].our_headsign == "ends here"
