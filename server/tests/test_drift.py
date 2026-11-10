from busboard.departures import Departure
from busboard.drift import DriftModel
from busboard.observe import ObservedDeparture

from conftest import local

T0 = local(2026, 11, 7, 9, 0)


def dep(trip, est, src=2, stop_id="0:1117", sched=None, timepoint=False):
    return Departure("20261106", trip, stop_id, 2, "4", "4", "", "Pulse", False, sched or est, est, src,
                     None, None, False, False, False, timepoint=timepoint)


def teach(model, index, trip, actual_late_s, stop_id="0:1117"):
    """Predictions at 30, 20 and 10 min ahead that all said T0+1800; the bus left actual_late_s later."""
    for lead in (1800, 1200, 600):
        model.record(index, [dep(trip, T0 + 1800, stop_id=stop_id)], T0 + 1800 - lead)
    actual = T0 + 1800 + actual_late_s
    model.resolve(ObservedDeparture("20261106", trip, stop_id, 2, T0 + 1800, actual - 10, actual + 10, "V", False))


def test_learns_a_steady_offset_regardless_of_lead(index):
    m = DriftModel(shrink_departures=0)
    actual = T0 + 3600
    # Buses left 90 s after every prediction, however far ahead it was made (as measured 2026-11-07..09)
    for trip in ("T_STOP", "T_BLOCKMATE"):
        for lead in (1800, 1200, 600):
            m.record(index, [dep(trip, actual - 90)], actual - lead)
        m.resolve(ObservedDeparture("20261106", trip, "0:1117", 2, actual, actual - 10, actual + 10, "V", False))
    offset, n = m.offset(m.key_of(index, "T_STOP", "0:1117"), actual)
    assert n == 2 and abs(offset - 90) < 1
    now = actual + 60
    for lead_s in (300, 1800):
        (d,) = m.correct(index, [dep("T_STOP", now + lead_s)], now)
        assert abs(d.drift_s - 90) <= 1


def test_correction_fades_out_at_the_last_minute(index):
    m = DriftModel(shrink_departures=0)
    teach(m, index, "T_STOP", 120)
    now = T0 + 3600
    (near,) = m.correct(index, [dep("T_STOP", now + 45)], now)
    (mid,) = m.correct(index, [dep("T_STOP", now + 120)], now)
    (far,) = m.correct(index, [dep("T_STOP", now + 600)], now)
    assert near.drift_s == 0 and abs(mid.drift_s - 60) <= 1 and abs(far.drift_s - 120) <= 1


def test_correction_is_learned_shrunk_and_capped(index):
    m = DriftModel(shrink_departures=3)
    teach(m, index, "T_STOP", 180)  # 3 min late against all three predictions
    offset, n = m.offset(m.key_of(index, "T_STOP", "0:1117"), T0 + 1800)
    assert n == 1 and abs(offset - 45) < 1  # one departure: a quarter of it
    now = T0 + 3600
    (d,) = m.correct(index, [dep("T_STOP", now + 1200)], now)
    assert d.drift_s > 0 and d.est == now + 1200 + d.drift_s and d.drift_s <= 300
    # Other stops and schedule-only predictions are untouched
    (other,) = m.correct(index, [dep("T_STOP", now + 1200, stop_id="0:900")], now)
    (sched,) = m.correct(index, [dep("T_STOP", now + 1200, src=0)], now)
    assert other.drift_s == 0 and sched.drift_s == 0


def test_feed_glitches_and_vague_departures_arent_learned(index):
    m = DriftModel(shrink_departures=0)
    # 100 min of lateness inherited from a bus that idled (seen 2026-11-09), then a departure known only to +/-3 min
    m.record(index, [dep("T_STOP", T0 + 7800)], T0 + 1800 - 600)
    m.resolve(ObservedDeparture("20261106", "T_STOP", "0:1117", 2, T0 + 1800, T0 + 1790, T0 + 1810, "V", False))
    m.record(index, [dep("T_BLOCKMATE", T0 + 2000)], T0 + 1400)
    m.resolve(ObservedDeparture("20261106", "T_BLOCKMATE", "0:1117", 2, T0 + 2000, T0 + 1950, T0 + 2310, "V", False))
    assert m.offset(m.key_of(index, "T_STOP", "0:1117"), T0 + 2400) == (0.0, 0)


def test_early_correction_respects_timepoints_and_window(index):
    m = DriftModel(shrink_departures=0, window_s=3600)
    teach(m, index, "T_STOP", -240)  # buses here leave earlier than predicted
    now = T0 + 2000
    (held,) = m.correct(index, [dep("T_STOP", now + 1200, sched=now + 1200, timepoint=True)], now)
    assert held.est == now + 1200 and held.held_at_timepoint  # never earlier than scheduled at a timepoint
    (early,) = m.correct(index, [dep("T_STOP", now + 1200)], now)
    assert early.drift_s < 0
    m.prune(T0 + 1800 + 3600 + 60)  # the departure has aged out of the window
    assert m.offset(m.key_of(index, "T_STOP", "0:1117"), T0 + 1800 + 3600 + 60) == (0.0, 0)


def test_warm_start_from_stored_rows(index):
    m = DriftModel(shrink_departures=0)
    rows = [{"ts": T0 + 1800 - lead, "service_date": "20261106", "trip_id": "T_STOP", "stop_code": "1117",
             "stop_sequence": 2, "our_src": 2, "raw_est": T0 + 1800, "actual": T0 + 1980} for lead in (1800, 1200, 600)]
    assert m.warm_start(index, rows) == 3
    assert m.offset(m.key_of(index, "T_STOP", "0:1117"), T0 + 1980)[0] > 0
