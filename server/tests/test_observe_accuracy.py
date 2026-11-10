import json
import re

from busboard import accuracy
from busboard.archive import InputArchive
from busboard.config import EXAMPLE_CONFIG
from busboard.compare import NO_UCOP, CompareStore, compare, no_ucop_rows
from busboard.observe import DepartureObserver
from busboard.realtime import parse_vehicle_positions

from conftest import IN_TRANSIT_TO, STOPPED_AT, local, make_zip, trip_updates, vehicle_positions
from test_ucop_compare import arr, dep


def positions(trip, vid, ts, stop, status=IN_TRANSIT_TO):
    return parse_vehicle_positions(vehicle_positions({trip: (vid, ts, stop, status)}))[1]


def test_departure_seen_when_bus_moves_past_the_stop(index):
    obs = DepartureObserver({"0:1117"})
    t = local(2026, 11, 6, 18, 0)
    assert obs.update(index, positions("T_STOP", "V1", t, "0:1112", STOPPED_AT)) == []  # waiting at the start
    assert obs.update(index, positions("T_STOP", "V1", t + 200, "0:1117")) == []  # heading to our stop
    assert obs.update(index, positions("T_STOP", "V1", t + 290, "0:1117", STOPPED_AT)) == []
    assert obs.update(index, positions("T_STOP", "V1", t + 290, "0:1117", STOPPED_AT)) == []  # no new report
    (d,) = obs.update(index, positions("T_STOP", "V1", t + 330, "0:900"))  # left
    assert (d.trip_id, d.stop_sequence, d.service_date) == ("T_STOP", 2, "20261106")
    assert (d.departed_after, d.departed_by, d.actual, d.uncertainty_s) == (t + 290, t + 330, t + 310, 20)
    assert d.seen_at_stop and d.sched == local(2026, 11, 6, 18, 5)
    assert obs.update(index, positions("T_STOP", "V1", t + 400, "0:900")) == []  # recorded once


def test_loop_trip_passing_the_stop_twice(index):
    obs = DepartureObserver({"0:1117"})
    t = local(2026, 11, 6, 19, 27)
    seen = []
    for dt_, stop in [(0, "0:1117"), (180, "0:900"), (600, "0:1117"), (1300, "0:1112")]:
        seen += obs.update(index, positions("T_LOOP", "V2", t + dt_, stop))
    assert [(d.stop_sequence, d.departed_after, d.departed_by) for d in seen] == [(2, t, t + 180), (4, t + 600, t + 1300)]


def test_first_seen_already_past_has_no_lower_bound(index):
    obs = DepartureObserver({"0:1117"})
    (d,) = obs.update(index, positions("T_STOP", "V1", local(2026, 11, 6, 18, 10), "0:900"))
    assert d.departed_after is None and d.uncertainty_s is None


def test_reports_before_the_trip_starts_are_not_departures(index):
    # A bus assigned early reports stops on the way to the first one, then goes back (seen 2026-11-09)
    obs = DepartureObserver({"0:1117"})
    for mins, stop in [(40, "0:900"), (41, "0:1117"), (45, "0:1112")]:
        assert obs.update(index, positions("T_STOP", "V1", local(2026, 11, 6, 17, mins), stop)) == []
    assert obs.update(index, positions("T_STOP", "V1", local(2026, 11, 6, 18, 6), "0:1117", STOPPED_AT)) == []
    (d,) = obs.update(index, positions("T_STOP", "V1", local(2026, 11, 6, 18, 7), "0:900"))
    assert (d.departed_after, d.departed_by) == (local(2026, 11, 6, 18, 6), local(2026, 11, 6, 18, 7))


def test_impossible_jump_along_the_route_is_not_a_departure(index):
    # In 30 s from the first stop to past the watched one, on a leg scheduled for 5+ min
    obs = DepartureObserver({"0:1117"})
    t = local(2026, 11, 6, 18, 0)
    assert obs.update(index, positions("T_STOP", "V1", t, "0:1112", STOPPED_AT)) == []
    assert obs.update(index, positions("T_STOP", "V1", t + 30, "0:900")) == []
    assert obs.update(index, positions("T_STOP", "V1", t + 900, "0:900")) == []


def test_long_last_leg_is_not_a_jump(index):
    # Heading to the far stop only means it just left ours
    obs = DepartureObserver({"0:1117"})
    t = local(2026, 11, 6, 18, 5)
    obs.update(index, positions("T_STOP", "V1", t, "0:1117", STOPPED_AT))
    (d,) = obs.update(index, positions("T_STOP", "V1", t + 30, "0:900"))
    assert d.stop_sequence == 2


def test_forget_resets_for_the_next_days_run(index):
    obs = DepartureObserver({"0:1117"})
    obs.update(index, positions("T_STOP", "V1", local(2026, 11, 6, 18, 10), "0:900"))
    obs.forget_older_than(local(2026, 11, 7, 0, 0))
    obs.update(index, positions("T_STOP", "V1", local(2026, 11, 7, 18, 4), "0:1117"))
    (d,) = obs.update(index, positions("T_STOP", "V1", local(2026, 11, 7, 18, 6), "0:900"))
    assert d.service_date == "20261107"


def test_accuracy_query_and_no_ucop_rows(tmp_path, index):
    store = CompareStore(tmp_path / "c.db")
    now = local(2026, 11, 6, 17, 55)
    ours = [dep("T_STOP", local(2026, 11, 6, 18, 5))]
    ours[0].stop_sequence = 2
    rows = compare(ours, [arr("T_STOP", local(2026, 11, 6, 18, 7))], now, tolerance_s=120, past_grace_s=60,
                   window_ahead_s=3600)
    store.record("1117", now, rows, 1, 1, None)
    obs = DepartureObserver({"0:1117"})
    obs.update(index, positions("T_STOP", "V1", local(2026, 11, 6, 18, 6), "0:1117", STOPPED_AT))
    seen = obs.update(index, positions("T_STOP", "V1", local(2026, 11, 6, 18, 7), "0:900"))
    store.record_departures(seen, {"0:1117": "1117"}, {"T_STOP": "4"})
    store.record_departures(seen, {"0:1117": "1117"}, {"T_STOP": "4"})  # duplicates ignored
    assert store.observed_count("1117", now) == 1
    acc = store.accuracy("1117", now - 60)["5-20 min"]  # actual 18:06:30, predicted 11.5 min ahead
    assert acc["n"] == 1 and acc["mae"] == 90 and acc["bias"] == -90
    assert acc["ucop_n"] == 1 and acc["ucop_mae"] == 60  # UCOP 18:07 shown, centred to 18:07:30

    logged = no_ucop_rows(ours, now, 3600)
    assert [(r.category, r.trip_id, r.stop_sequence) for r in logged] == [(NO_UCOP, "T_STOP", 2)]


def test_accuracy_tool_end_to_end(tmp_path, capsys):
    data = tmp_path / "data"
    archive = InputArchive(data)
    zip_path = tmp_path / "gtfs.zip"
    zip_path.write_bytes(make_zip())
    name = archive.save_static(zip_path, '"v1"', local(2026, 11, 6, 17, 0), keep=5)
    tu = trip_updates({"T_STOP": {2: local(2026, 11, 6, 18, 8)}})
    steps = [(local(2026, 11, 6, 17, 56), "0:1112", STOPPED_AT), (local(2026, 11, 6, 18, 4), "0:1117", IN_TRANSIT_TO),
             (local(2026, 11, 6, 18, 7), "0:1117", STOPPED_AT), (local(2026, 11, 6, 18, 9), "0:900", IN_TRANSIT_TO)]
    for ts, stop, status in steps:
        vp = vehicle_positions({"T_STOP": ("V1", ts - 5, stop, status)})
        archive.save_snapshot(ts, {"ts": ts, "static": name, "stops": [], "ucop": {}},
                              {"TripUpdate.pb": tu, "VehiclePosition.pb": vp})
    cfg = tmp_path / "config.toml"  # the template config, limited to one stop
    cfg.write_text(re.sub(r"^stops = \[.*?\]", 'stops = ["1117"]', EXAMPLE_CONFIG.read_text(), flags=re.S | re.M))
    rows = tmp_path / "acc.jsonl"
    assert accuracy.main([str(data), "--config", str(cfg), "--rows", str(rows), "--max-uncertainty", "120"]) == 0
    scored = [json.loads(l) for l in rows.read_text().splitlines()]
    # Departure between 18:06:55 and 18:08:55; predictions said 18:08 from 17:56, 18:04 and 18:07
    assert {r["source"] for r in scored} == {"ours"} and len(scored) == 3
    assert all(r["trip_id"] == "T_STOP" and r["error_s"] == local(2026, 11, 6, 18, 8) - r["actual"] for r in scored)
    assert "1 observed departures" in capsys.readouterr().out
