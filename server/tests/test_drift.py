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


def test_learns_drift_that_grows_with_lead(index):
    m = DriftModel(shrink_departures=0)
    actual = T0 + 3600
    # Each prediction was early by 6 s per minute of lead (0.1 s per s), as on routes 7/8/9 on 2026-11-07
    for trip in ("T_STOP", "T_BLOCKMATE"):
        for lead in (1800, 1200, 600):
            m.record(index, [dep(trip, actual - lead // 10)], actual - lead)
        m.resolve(ObservedDeparture("20261106", trip, "0:1117", 2, actual, actual - 10, actual + 10, "V", False))
    rate, n = m.rate(m.key_of(index, "T_STOP", "0:1117"), actual)
    assert n == 2 and abs(rate - 0.1) < 0.01
    now = actual + 60
    (d,) = m.correct(index, [dep("T_STOP", now + 1200)], now)  # 20 min ahead -> 2 min later
    assert abs(d.drift_s - 120) <= 2


def test_correction_is_learned_shrunk_and_capped(index):
    m = DriftModel(shrink_departures=3)
    teach(m, index, "T_STOP", 180)  # 3 min late against all three predictions
    rate, n = m.rate(m.key_of(index, "T_STOP", "0:1117"), T0 + 1800)
    assert n == 1 and 0 < rate <= 0.25
    now = T0 + 3600
    (d,) = m.correct(index, [dep("T_STOP", now + 1200)], now)
    assert d.drift_s > 0 and d.est == now + 1200 + d.drift_s and d.drift_s <= 300
    # Other stops and schedule-only predictions are untouched
    (other,) = m.correct(index, [dep("T_STOP", now + 1200, stop_id="0:900")], now)
    (sched,) = m.correct(index, [dep("T_STOP", now + 1200, src=0)], now)
    assert other.drift_s == 0 and sched.drift_s == 0


def test_early_correction_respects_timepoints_and_window(index):
    m = DriftModel(shrink_departures=0, window_s=3600)
    teach(m, index, "T_STOP", -240)  # buses here leave earlier than predicted
    now = T0 + 2000
    (held,) = m.correct(index, [dep("T_STOP", now + 1200, sched=now + 1200, timepoint=True)], now)
    assert held.est == now + 1200 and held.held_at_timepoint  # never earlier than scheduled at a timepoint
    (early,) = m.correct(index, [dep("T_STOP", now + 1200)], now)
    assert early.drift_s < 0
    m.prune(T0 + 1800 + 3600 + 60)  # the departure has aged out of the window
    assert m.rate(m.key_of(index, "T_STOP", "0:1117"), T0 + 1800 + 3600 + 60) == (0.0, 0)


def test_warm_start_from_stored_rows(index):
    m = DriftModel(shrink_departures=0)
    rows = [{"ts": T0 + 1800 - lead, "service_date": "20261106", "trip_id": "T_STOP", "stop_code": "1117",
             "stop_sequence": 2, "our_src": 2, "raw_est": T0 + 1800, "actual": T0 + 1980} for lead in (1800, 1200, 600)]
    assert m.warm_start(index, rows) == 3
    assert m.rate(m.key_of(index, "T_STOP", "0:1117"), T0 + 1980)[0] > 0
