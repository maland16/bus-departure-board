import httpx
import pytest

from busboard.static_gtfs import StaticFeed, tidy_headsign

from conftest import local, make_zip


@pytest.fixture
def feed_with_bad_cache(tmp_path):
    feed = StaticFeed("http://feed.test/gtfs.zip", tmp_path, ["1117"])
    feed.zip_path.parent.mkdir(parents=True)
    feed.zip_path.write_bytes(b"not a zip")
    feed.meta_path.write_text('{"etag": "old"}')
    return feed


def test_corrupt_cached_zip_is_replaced_by_a_full_download(feed_with_bad_cache, monkeypatch):
    feed = feed_with_bad_cache
    sent = {}

    def fake_get(url, headers, **_kwargs):
        sent.update(headers)
        return httpx.Response(200, content=make_zip(), headers={"etag": "new"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("busboard.static_gtfs.httpx.get", fake_get)
    assert feed.load_cached() is False
    assert feed.index is None
    assert feed.refresh() is True
    assert "If-None-Match" not in sent  # the server would answer 304 and leave the bad zip in place
    assert feed.index is not None
    assert feed.load_cached() is True


def test_corrupt_cached_zip_with_no_network_does_not_raise(feed_with_bad_cache, monkeypatch):
    feed = feed_with_bad_cache

    def fake_get(*_args, **_kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr("busboard.static_gtfs.httpx.get", fake_get)
    assert feed.load_cached() is False
    assert feed.refresh() is False
    assert feed.index is None
    assert "ConnectError" in feed.last_error


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
