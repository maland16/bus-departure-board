from pathlib import Path

from busboard.compare import MATCH, OURS_ONLY, TIME_DIFF, UCOP_DEPARTED, UCOP_ONLY, CompareStore, compare
from busboard.departures import Departure
from busboard.ucop import UcopArrival, clock_to_epoch, parse_stop_page

from conftest import TZ, local

FIXTURE = Path(__file__).parent / "fixtures" / "ucop_stop_1117.html"
FETCHED = local(2026, 11, 6, 19, 31)


def test_parse_real_page():
    arrivals = parse_stop_page(FIXTURE.read_text(), FETCHED, TZ)
    assert len(arrivals) == FIXTURE.read_text().count('class="arrival"') == 40
    first = arrivals[0]
    assert (first.service_date, first.trip_id, first.stop_id) == ("20261106", "School Daygdop_NNSKDn", "0:1117")
    assert (first.route, first.headsign, first.status) == ("BRZ", "Brunswick Train Station", "late")
    assert first.sched == local(2026, 11, 6, 19, 2) and first.est == local(2026, 11, 6, 20, 18)

    by_trip = {a.trip_id: a for a in arrivals}
    assert by_trip["School DayeT9MCCdTa-T"].departed
    falmouth = by_trip["School Dayey8pz1l"]
    assert falmouth.headsign == "Falmouth Shaw's" and falmouth.countdown == "Departs in 3 min"
    on_time = by_trip["School DayJ8Z-nkd6hy8"]  # single time + "On Time"
    assert (on_time.status, on_time.sched, on_time.est) == ("ok", local(2026, 11, 6, 19, 45), local(2026, 11, 6, 19, 45))
    assert on_time.headsign == "Fort Rd & SMCC"
    sched_only = by_trip["School DaymOOwJvnCMPX"]
    assert sched_only.status is None and not sched_only.realtime
    assert by_trip["School Dayqjw8vlq"].headsign == "ends here"


def test_clock_after_midnight_picks_nearest_day():
    near = local(2026, 11, 6, 23, 50)
    assert clock_to_epoch("12:15 am", "20261106", near, TZ) == local(2026, 11, 7, 0, 15)
    assert clock_to_epoch("11:55 pm", "20261106", near, TZ) == local(2026, 11, 6, 23, 55)
    assert clock_to_epoch("soon", "20261106", near, TZ) is None


def dep(trip, est, sched=None, src=2, stale=False):
    return Departure("20261106", trip, "0:1117", 2, "4", "4", "", "Metro Pulse", False, sched or est, est, src,
                     None, None, stale, False, False)


def arr(trip, est, status="ok", sched=None):
    return UcopArrival("20261106", trip, "0:1117", "4", "Metro Pulse", sched or est, est, status, "", "")


def test_compare_categories():
    now = local(2026, 11, 6, 18, 0)
    ours = [
        dep("A", local(2026, 11, 6, 18, 5, 40)),  # UCOP shows 18:05 -> within tolerance
        dep("B", local(2026, 11, 6, 18, 10)),
        dep("C", local(2026, 11, 6, 18, 20)),  # missing from UCOP
        dep("E", local(2026, 11, 6, 17, 50), stale=True, src=0),  # we reset a ghost bus to schedule
        dep("F", local(2026, 11, 6, 21, 0)),  # beyond the end of UCOP's list: ignored
        dep("G", local(2026, 11, 6, 17, 30)),  # long gone, UCOP doesn't list it: ignored
    ]
    ucop = [
        arr("A", local(2026, 11, 6, 18, 5)),
        arr("B", local(2026, 11, 6, 18, 15), status="late"),
        arr("D", local(2026, 11, 6, 18, 25)),  # missing from ours
        arr("E", local(2026, 11, 6, 18, 30), status="late"),
        arr("H", local(2026, 11, 6, 17, 58), status="idle"),
        arr("Z", local(2026, 11, 6, 19, 0)),
    ]
    rows = {r.trip_id: r for r in compare(ours, ucop, now, tolerance_s=90, past_grace_s=60, window_ahead_s=3 * 3600)}
    assert rows["A"].category == MATCH and rows["A"].diff_s == 40
    assert rows["B"].category == TIME_DIFF and rows["B"].diff_s == -300
    assert rows["C"].category == OURS_ONLY
    assert rows["D"].category == UCOP_ONLY
    assert rows["E"].category == TIME_DIFF and rows["E"].stale_vehicle
    assert rows["H"].category == UCOP_DEPARTED
    assert "F" not in rows and "G" not in rows


def test_store_roundtrip(tmp_path):
    store = CompareStore(tmp_path / "c.db")
    now = local(2026, 11, 6, 18, 0)
    rows = compare([dep("A", now + 300), dep("B", now + 600)],
                   [arr("A", now + 300), arr("B", now + 900, status="late")],
                   now, tolerance_s=90, past_grace_s=60, window_ahead_s=3600)
    store.record("1117", now, rows, 2, 2, None)
    s = store.summary("1117", now - 10)
    assert (s["all"]["match"], s["all"]["time_diff"], s["all"]["snapshots"]) == (1, 1, 1)
    assert s["near_term"]["rows"] == 2
    snap, latest = store.latest("1117")
    assert snap["ucop_count"] == 2 and len(latest) == 2
    assert [r["trip_id"] for r in store.recent_mismatches("1117", now - 10)] == ["B"]


def test_diff_reasons():
    from busboard.compare import CompareRow
    base = dict(service_date="20261106", trip_id="T", route="4", category=TIME_DIFF, our_est=0, ucop_est=600,
                our_src=2, rt_updates=10, ucop_status="late")
    assert CompareRow(**base, stale_vehicle=True, held_at_timepoint=True).diff_reason == "stale_vehicle"
    assert CompareRow(**base, held_at_timepoint=True).diff_reason == "held_at_timepoint"
    assert CompareRow(**{**base, "rt_updates": None, "our_src": 0}).diff_reason == "gone_from_feed"
    assert CompareRow(**{**base, "rt_updates": None, "our_src": 0, "ucop_status": None}).diff_reason == "src0"
    assert CompareRow(**base).diff_reason == "src2"
    assert CompareRow(**{**base, "category": MATCH}).diff_reason is None


def test_window_edges_are_not_counted():
    """Seen overnight: trips exactly 3 h out listed only by UCOP (it truncates to the minute), and a
    bus we still showed 45 s after it left while UCOP had dropped it."""
    now = local(2026, 11, 7, 2, 17, 10)
    edge = arr("EDGE", local(2026, 11, 7, 5, 17))  # really 5:17:30, past our 3 h window ending 5:17:10
    gone = dep("GONE", now - 45)
    later = arr("LATER", now + 3600)
    rows = {r.trip_id: r for r in compare([gone], [edge, later], now, tolerance_s=120, past_grace_s=60,
                                          window_ahead_s=3 * 3600)}
    assert "EDGE" not in rows and "GONE" not in rows


def test_clock_on_tomorrows_service_date_stays_tomorrow():
    # Seen at stop 586 on a Saturday morning: UCOP's list reached Sunday's 8:47 am trip
    near = local(2026, 11, 7, 7, 55)
    assert clock_to_epoch("8:47 am", "20261108", near, TZ) == local(2026, 11, 8, 8, 47)
    # First trips of the day just after midnight still land on their own date
    assert clock_to_epoch("12:30 am", "20261107", local(2026, 11, 6, 23, 50), TZ) == local(2026, 11, 7, 0, 30)
