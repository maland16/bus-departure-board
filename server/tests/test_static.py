from busboard.static_gtfs import tidy_headsign

from conftest import local


def test_tidy_headsign():
    assert tidy_headsign("FALMOUTH SHAW'S") == "Falmouth Shaw's"
    assert tidy_headsign("MAINE MALL JC PENNEY") == "Maine Mall JC Penney"
    assert tidy_headsign("FORT RD & SMCC") == "Fort Rd & SMCC"
    assert tidy_headsign("295 GANNETT DR") == "295 Gannett Dr"
    assert tidy_headsign("RIVERSIDE ST + HANNAFORD") == "Riverside St + Hannaford"
    assert tidy_headsign("WEST-END  LOOP") == "West-End Loop"
    assert tidy_headsign("THOMAS ST + Millcreek Hub") == "Thomas St + Millcreek Hub"  # seen in the real feed
    # Mixed case is left alone
    assert tidy_headsign("Congress St + Park St") == "Congress St + Park St"
    assert tidy_headsign("McDonald's") == "McDonald's"
    assert tidy_headsign("") == ""


def test_stop_code_prefers_boarding_stop_over_parent_station(index):
    assert index.stop_id_for("1117") == "0:1117"
    assert index.stop_id_for("9999") is None


def test_headsign_fallbacks(index):
    assert index.trips["T_STOP"].headsign == "Falmouth Shaw's"
    assert index.trips["T_ORIGIN"].headsign == "Prides Church"  # blank -> last stop's name
    assert index.trips["T_ENDS"].headsign == "Congress St + Center St"


def test_trip_origin_and_last_stop(index):
    t = index.trips["T_LOOP"]
    assert (t.origin_stop_sequence, t.last_stop_sequence) == (1, 5)
    assert t.origin_departure_secs == 19 * 3600 + 28 * 60


def test_scheduled_between_and_loop_visits(index):
    visits = index.scheduled_between("0:1117", local(2026, 11, 6, 19, 0), local(2026, 11, 6, 20, 0))
    assert [(v.trip.trip_id, v.visit.stop_sequence) for v in visits] == [
        ("T_SCHED", 2), ("T_LOOP", 2), ("T_LOOP", 4)
    ]
    assert visits[0].sched == local(2026, 11, 6, 19, 2)


def test_after_midnight_trip_belongs_to_previous_service_day(index):
    visits = index.scheduled_between("0:1117", local(2026, 11, 7, 0, 0), local(2026, 11, 7, 1, 0))
    assert [(v.trip.trip_id, v.service_date) for v in visits] == [("T_NIGHT", "20261106")]
    assert visits[0].sched == local(2026, 11, 7, 0, 30)


def test_dst_change_day(index):
    # Clocks went back at 2 AM on 2026-11-01; GTFS times count from noon minus 12h, so 18:05 is still 18:05
    visits = index.scheduled_between("0:1117", local(2026, 11, 1, 18, 0), local(2026, 11, 1, 18, 10))
    assert visits[0].sched == local(2026, 11, 1, 18, 5)


def test_no_service_on_dates_missing_from_calendar_dates(index):
    assert index.scheduled_between("0:1117", local(2026, 11, 3, 0, 0), local(2026, 11, 3, 23, 59)) == []


def test_ends_here(index):
    visits = index.scheduled_between("0:1117", local(2026, 11, 6, 20, 5), local(2026, 11, 6, 20, 15))
    assert visits[0].trip.trip_id == "T_ENDS" and visits[0].ends_here
