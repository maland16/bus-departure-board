import json
import sqlite3

from busboard.archive import EventLog, InputArchive, replay_inputs
from busboard.config import EXAMPLE_CONFIG, load_config
from busboard.departures import Departure, compute_departures
from busboard.store import Store

from conftest import local, make_zip, trip_updates, vehicle_positions


def dep(trip, seq, est, sched=None, src=2, drift_s=0):
    d = Departure("20261106", trip, "0:1117", seq, "4", "4", "", "Pulse", False, sched or est, est, src,
                  None, None, False, False, False)
    d.drift_s = drift_s
    return d


def test_event_log(tmp_path):
    log = EventLog(tmp_path)
    log.write("static_index", trips_kept=10, trips_added=2)
    (path,) = (tmp_path / "logs").glob("events-*.jsonl")
    rec = json.loads(path.read_text())
    assert rec["event"] == "static_index" and rec["trips_kept"] == 10


def test_compare_db_is_migrated_to_predictions(tmp_path):
    """The old UCOP comparison database: our side of each row is kept, the rest dropped."""
    old = tmp_path / "compare.db"
    con = sqlite3.connect(old)
    con.executescript("""
      CREATE TABLE snapshots (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, stop_code TEXT NOT NULL,
        ucop_count INTEGER, ours_count INTEGER, ucop_error TEXT, archive TEXT);
      CREATE TABLE rows (snapshot_id INTEGER NOT NULL, ts INTEGER NOT NULL, stop_code TEXT NOT NULL,
        service_date TEXT, trip_id TEXT, route TEXT, category TEXT, our_sched INTEGER, our_est INTEGER,
        our_src INTEGER, stale_vehicle INTEGER, stale_block INTEGER, ucop_sched INTEGER, ucop_est INTEGER,
        ucop_status TEXT, diff_s INTEGER, our_headsign TEXT, ucop_headsign TEXT, headsign_differs INTEGER,
        visit INTEGER, stop_sequence INTEGER, vehicle_id TEXT, drift_s INTEGER);
      CREATE TABLE observed_departures (service_date TEXT NOT NULL, trip_id TEXT NOT NULL,
        stop_code TEXT NOT NULL, stop_sequence INTEGER NOT NULL, stop_id TEXT, route TEXT, sched INTEGER,
        departed_after INTEGER, departed_by INTEGER NOT NULL, vehicle_id TEXT, seen_at_stop INTEGER,
        PRIMARY KEY (service_date, trip_id, stop_code, stop_sequence));
      INSERT INTO snapshots (id, ts, stop_code) VALUES (1, 1000, '1117');
      INSERT INTO rows (snapshot_id, ts, stop_code, service_date, trip_id, route, category, our_sched, our_est,
        our_src, stale_vehicle, stop_sequence, drift_s, ucop_est) VALUES
        (1, 1000, '1117', '20261106', 'A', '4', 'match', 1600, 1660, 2, 0, 2, 30, 1650),
        (1, 1000, '1117', '20261106', 'B', '4', 'no_ucop', 1700, 1700, 0, 0, 2, 0, NULL),
        (1, 1000, '1117', '20261106', 'C', '4', 'ucop_only', NULL, NULL, NULL, NULL, NULL, NULL, 1800),
        (1, 1000, '1117', '20261106', 'D', '4', 'ours_terminating', 1900, 1900, 2, 0, 3, 0, NULL);
      INSERT INTO observed_departures VALUES ('20261106', 'A', '1117', 2, '0:1117', '4', 1600, 1680, 1700, 'V', 1);
    """)
    con.commit()
    con.close()

    store = Store(tmp_path / "busboard.db")
    assert not old.exists() and (tmp_path / "busboard.db").exists()
    m = store.migrate_legacy()
    assert (m["comparison_rows"], m["predictions_kept"]) == (4, 2)
    db = sqlite3.connect(tmp_path / "busboard.db")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"predictions", "observed_departures"}
    rows = db.execute("SELECT trip_id, est, src, drift_s FROM predictions ORDER BY trip_id").fetchall()
    assert rows == [("A", 1660, 2, 30), ("B", 1700, 0, 0)]
    # The drift model can warm-start from the migrated rows: raw estimate = logged - drift
    (t,) = store.drift_training(0)
    assert (t["trip_id"], t["raw_est"], t["actual"]) == ("A", 1630, 1690)
    assert store.migrate_legacy() is None  # only once


def test_predictions_roundtrip_and_accuracy(tmp_path):
    store = Store(tmp_path / "busboard.db")
    assert store.migrate_legacy() is None
    now = local(2026, 11, 6, 17, 55)
    deps = [dep("T_STOP", 2, local(2026, 11, 6, 18, 5)), dep("T_ORIGIN", 2, local(2026, 11, 6, 18, 36), src=1)]
    assert store.record_predictions("1117", now, deps, {("T_STOP", 2)}) == 2
    db = sqlite3.connect(tmp_path / "busboard.db")
    assert db.execute("SELECT trip_id, shown, src FROM predictions ORDER BY trip_id").fetchall() == [
        ("T_ORIGIN", 0, 1), ("T_STOP", 1, 2)]

    class Obs:  # an ObservedDeparture
        service_date, trip_id, stop_id, stop_sequence, sched = "20261106", "T_STOP", "0:1117", 2, 0
        departed_after, departed_by, vehicle_id, seen_at_stop = local(2026, 11, 6, 18, 6), local(2026, 11, 6, 18, 7), "V", True

    store.record_departures([Obs], {"0:1117": "1117"}, {"T_STOP": "4"})
    store.record_departures([Obs], {"0:1117": "1117"}, {"T_STOP": "4"})  # duplicates ignored
    assert store.counts_since(now) == {"1117": {"predictions": 2, "observed": 1}}
    acc = store.accuracy(now - 60)["1117"]  # actual 18:06:30, predicted 18:05 11.5 min ahead
    assert acc["n"] == 1 and acc["mae"] == 90 and acc["bias"] == -90
    store.prune(0)
    assert store.counts_since(0) == {}


def test_replay_inputs(tmp_path):
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
    # Older snapshots also hold UCOP's page: ignored
    archive.save_snapshot(now, {"ts": now, "static": name, "static_version": '"etag-1"'},
                          {"TripUpdate.pb": tu, "VehiclePosition.pb": vp, "ucop-1117.html": b"<html></html>"})
    assert archive.last_saved == now and archive.days() == ["20261106"]
    assert archive.measure() > 0

    cfg = load_config(EXAMPLE_CONFIG)
    ((snap, index, rt, stops),) = list(replay_inputs(archive, cfg, stops_filter=["1117"]))
    assert snap.ts == now and stops == ["1117"]
    deps = {d.trip_id: d for d in compute_departures(index, rt, "1117", now, window_ahead_s=3600, past_grace_s=60,
                                                     stale_vehicle_s=900)}
    assert deps["T_STOP"].est == local(2026, 11, 6, 18, 8) and deps["T_STOP"].vehicle_age_s == 20
    assert deps["T_ORIGIN"].origin_delay_s == 240
