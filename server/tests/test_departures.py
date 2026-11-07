from busboard.departures import SRC_ORIGIN, SRC_SCHEDULE, SRC_STOP, board_departures, compute_departures
from busboard.realtime import RealtimeSnapshot, parse_trip_updates, parse_vehicle_positions

from conftest import local, trip_updates, vehicle_positions

NOW = local(2026, 11, 6, 17, 55)


def snapshot(trips, vehicles=None, positions=None) -> RealtimeSnapshot:
    _, t = parse_trip_updates(trip_updates(trips, vehicles))
    _, v = parse_vehicle_positions(vehicle_positions(positions or {}))
    return RealtimeSnapshot(trips=t, vehicles_by_trip=v)


def run(index, snap, now=NOW, **kw):
    args = dict(window_ahead_s=3 * 3600, past_grace_s=60, stale_vehicle_s=300)
    args.update(kw)
    return {(d.trip_id, d.stop_sequence): d for d in compute_departures(index, snap, "1117", now, **args)}


def test_three_sources(index):
    snap = snapshot(
        {
            "T_STOP": {1: local(2026, 11, 6, 18, 3), 2: local(2026, 11, 6, 18, 8), 3: local(2026, 11, 6, 18, 23)},
            "T_ORIGIN": {1: local(2026, 11, 6, 18, 34)},  # only the first stop, 4 min late
        },
        positions={"T_STOP": ("2645", NOW - 10)},
    )
    deps = run(index, snap)
    assert (deps["T_STOP", 2].src, deps["T_STOP", 2].est) == (SRC_STOP, local(2026, 11, 6, 18, 8))
    assert (deps["T_ORIGIN", 2].src, deps["T_ORIGIN", 2].est) == (SRC_ORIGIN, local(2026, 11, 6, 18, 36))
    assert (deps["T_SCHED", 2].src, deps["T_SCHED", 2].est) == (SRC_SCHEDULE, local(2026, 11, 6, 19, 2))
    assert deps["T_STOP", 2].delay_s == 180
    assert deps["T_STOP", 2].vehicle_id == "2645" and deps["T_STOP", 2].vehicle_age_s == 10


def test_loop_stop_matched_by_sequence(index):
    snap = snapshot({"T_LOOP": {2: local(2026, 11, 6, 19, 31), 4: local(2026, 11, 6, 19, 55)}})
    deps = run(index, snap)
    assert deps["T_LOOP", 2].est == local(2026, 11, 6, 19, 31)
    assert deps["T_LOOP", 4].est == local(2026, 11, 6, 19, 55)


def test_stale_vehicle_falls_back_to_schedule(index):
    # A bus that stopped reporting 34 minutes ago, predicted 30 min late (seen in the real feed)
    snap = snapshot(
        {"T_STOP": {2: local(2026, 11, 6, 18, 35)}},
        positions={"T_STOP": ("2645", NOW - 34 * 60)},
    )
    d = run(index, snap)["T_STOP", 2]
    assert (d.src, d.est, d.stale_vehicle) == (SRC_SCHEDULE, local(2026, 11, 6, 18, 5), True)
    # The next trip on the same block has no vehicle of its own: flagged, not changed
    assert run(index, snap)["T_BLOCKMATE", 2].stale_block


def test_past_departures_dropped_but_late_buses_kept(index):
    now = local(2026, 11, 6, 18, 10)
    snap = snapshot({"T_STOP": {2: local(2026, 11, 6, 18, 12)}})  # 7 min late, not gone yet
    deps = run(index, snap, now=now)
    assert ("T_STOP", 2) in deps
    snap = snapshot({"T_STOP": {2: local(2026, 11, 6, 18, 6)}})  # left 4 min ago
    assert ("T_STOP", 2) not in run(index, snap, now=now)


def test_realtime_for_another_day_is_ignored(index):
    snap = snapshot({"T_STOP": {2: local(2026, 11, 7, 18, 5)}})
    assert run(index, snap)["T_STOP", 2].src == SRC_SCHEDULE


def test_board_skips_trips_ending_here(index):
    deps = compute_departures(index, RealtimeSnapshot(), "1117", local(2026, 11, 6, 19, 45),
                              window_ahead_s=3600, past_grace_s=60, stale_vehicle_s=300)
    assert any(d.ends_here for d in deps)
    assert not any(d.ends_here for d in board_departures(deps, 8))


def test_unknown_stop(index):
    assert compute_departures(index, RealtimeSnapshot(), "4242", NOW,
                              window_ahead_s=3600, past_grace_s=60, stale_vehicle_s=300) == []


def test_delay_from_nearest_stop_with_a_prediction(index):
    # Seen in the real feed: a trip whose updates start mid-route, or only cover a later stop
    snap = snapshot({"T_LOOP": {3: local(2026, 11, 6, 19, 46)}})  # seq 3 scheduled 19:40 -> 6 min late
    deps = run(index, snap)
    assert (deps["T_LOOP", 4].src, deps["T_LOOP", 4].est, deps["T_LOOP", 4].ref_stop_sequence) == (
        SRC_ORIGIN, local(2026, 11, 6, 19, 56), 3)  # earlier stop preferred
    assert (deps["T_LOOP", 2].est, deps["T_LOOP", 2].ref_stop_sequence) == (local(2026, 11, 6, 19, 36), 3)
    assert deps["T_LOOP", 2].origin_delay_s is None


def test_held_at_timepoint_not_shown_leaving_early(index):
    snap = snapshot({
        "T_STOP": {2: local(2026, 11, 6, 17, 58)},  # timepoint, predicted 7 min early
        "T_SCHED": {2: local(2026, 11, 6, 18, 58)},  # not a timepoint: early is believable
        "T_ENDS": {2: local(2026, 11, 6, 20, 5)},  # last stop: an early arrival is fine
    })
    deps = run(index, snap)
    assert (deps["T_STOP", 2].est, deps["T_STOP", 2].held_at_timepoint) == (local(2026, 11, 6, 18, 5), True)
    assert (deps["T_SCHED", 2].est, deps["T_SCHED", 2].held_at_timepoint) == (local(2026, 11, 6, 18, 58), False)
    assert deps["T_ENDS", 2].est == local(2026, 11, 6, 20, 5)


def test_stop_sched_secs(index):
    assert index.stop_sched_secs("T_LOOP", 3) == 19 * 3600 + 40 * 60
    assert index.stop_sched_secs("T_LOOP", 9) is None
    assert index.stop_sched_secs("nope", 1) is None
    assert [v.timepoint for v in index.visits["0:1117"] if v.trip_id == "T_SCHED"] == [False]


def test_trips_missing_from_one_fetch_are_held():
    from busboard.realtime import TripRT, carry_over_missing_trips
    a, b = TripRT("A", {1: 100}, None), TripRT("B", {1: 200}, None)
    seen = {}
    assert set(carry_over_missing_trips({"A": a, "B": b}, seen, 1000, 120)) == {"A", "B"}
    assert carry_over_missing_trips({"A": a}, seen, 1030, 120)["B"] is b  # B dropped out briefly
    assert set(carry_over_missing_trips({"A": a}, seen, 1200, 120)) == {"A"}  # gone too long
    assert "B" not in seen


def test_estimate_from_bus_position_when_trip_missing_from_trip_updates(index):
    """Seen 2026-11-07: trips absent from TripUpdates for minutes while their bus ran 8 min late."""
    from conftest import IN_TRANSIT_TO
    now = local(2026, 11, 6, 18, 6)
    # T_STOP scheduled to leave 0:1112 (seq 1) at 18:00; at 18:05:50 its bus is still heading there
    _, v = parse_vehicle_positions(vehicle_positions({"T_STOP": ("2645", now - 10, "0:1112", IN_TRANSIT_TO)}))
    d = run(index, RealtimeSnapshot(vehicles_by_trip=v), now=now)["T_STOP", 2]
    assert (d.src, d.from_vehicle, d.ref_stop_sequence) == (SRC_ORIGIN, True, 1)
    assert d.est == local(2026, 11, 6, 18, 5) + 350  # 5 min 50 s late at the first stop, carried forward

    # The bus is already past our stop: don't show it on schedule
    _, v = parse_vehicle_positions(vehicle_positions({"T_STOP": ("2645", now - 10, "0:900", IN_TRANSIT_TO)}))
    assert ("T_STOP", 2) not in run(index, RealtimeSnapshot(vehicles_by_trip=v), now=now)

    # A ghost bus's position is ignored like its predictions
    _, v = parse_vehicle_positions(vehicle_positions({"T_STOP": ("2645", now - 3600, "0:1112", IN_TRANSIT_TO)}))
    d = run(index, RealtimeSnapshot(vehicles_by_trip=v), now=now)["T_STOP", 2]
    assert d.src == SRC_SCHEDULE and not d.from_vehicle
