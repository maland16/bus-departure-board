"""A tiny synthetic GTFS feed shaped like GP Metro's (calendar_dates only, blank headsigns, a
parent station sharing the stop code), plus helpers to build realtime feeds."""

import datetime as dt
import io
import os
import zipfile
from zoneinfo import ZoneInfo

import pytest
from google.transit import gtfs_realtime_pb2 as rt

from busboard.config import EXAMPLE_CONFIG
from busboard.static_gtfs import build_index

# Tests never read this machine's config.toml (it isn't in git, and holds the publish target)
os.environ["BUSBOARD_CONFIG"] = str(EXAMPLE_CONFIG)

TZ = ZoneInfo("America/New_York")


def local(y, mo, d, h, mi, s=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, s, tzinfo=TZ).timestamp())


TRIPS = {
    # trip_id: (route, headsign, [(stop_id, seq, time)])
    "T_STOP": ("4", "FALMOUTH SHAW'S", [("0:1112", 1, "18:00:00"), ("0:1117", 2, "18:05:00"), ("0:900", 3, "18:20:00")]),
    "T_ORIGIN": ("2", "", [("0:1112", 1, "18:30:00"), ("0:1117", 2, "18:32:00"), ("0:900", 3, "18:50:00")]),
    # 0:1117 is not a timepoint on this trip (4th field = GTFS timepoint)
    "T_SCHED": ("2", "", [("0:1112", 1, "19:00:00"), ("0:1117", 2, "19:02:00", "0"), ("0:900", 3, "19:20:00")]),
    "T_LOOP": ("2", "", [("0:1112", 1, "19:28:00"), ("0:1117", 2, "19:30:00"), ("0:900", 3, "19:40:00"),
                         ("0:1117", 4, "19:50:00"), ("0:1112", 5, "20:00:00")]),
    "T_ENDS": ("4", "", [("0:1112", 1, "20:00:00"), ("0:1117", 2, "20:10:00")]),
    "T_NIGHT": ("4", "FORT RD & SMCC", [("0:1112", 1, "24:25:00"), ("0:1117", 2, "24:30:00"), ("0:900", 3, "24:45:00")]),
    "T_BLOCKMATE": ("4", "FALMOUTH SHAW'S", [("0:1112", 1, "18:40:00"), ("0:1117", 2, "18:45:00"), ("0:900", 3, "19:00:00")]),
}
BLOCKS = {"T_STOP": "B1", "T_BLOCKMATE": "B1"}


def make_zip(service_dates=("20261101", "20261106", "20261107")) -> bytes:
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n1,GP Metro,https://example.com,America/New_York\n",
        "stops.txt": (
            "stop_id,stop_code,stop_name,location_type,parent_station\n"
            "1:1117,1117,Congress St + Center St (station),1,\n"
            "0:1117,1117,Congress St + Center St,0,1:1117\n"
            "0:1112,1112,Metro Pulse,0,\n"
            "0:900,900,Prides Church,0,\n"
        ),
        "routes.txt": "route_id,route_short_name,route_long_name,route_type,route_color\n"
                      "2,2,Forest Avenue - Riverton,3,FFC000\n4,4,Westbrook - Brighton Ave,3,92D050\n",
        "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id,block_id\n" + "".join(
            f"{r},SD,{t},{h},0,{BLOCKS.get(t, t)}\n" for t, (r, h, _) in TRIPS.items()
        ),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n" + "".join(
            f"{t},{st[2]},{st[2]},{st[0]},{st[1]},{st[3] if len(st) > 3 else ''}\n"
            for t, (_, _, sts) in TRIPS.items() for st in sts
        ),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n",
        "calendar_dates.txt": "service_id,date,exception_type\n" + "".join(f"SD,{d},1\n" for d in service_dates),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


@pytest.fixture
def index():
    return build_index(make_zip(), ["1117"], version="test")


def trip_updates(trips: dict[str, dict[int, int]], vehicles: dict[str, str] | None = None, header_ts=0) -> bytes:
    """trips: trip_id -> {stop_sequence: epoch}"""
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = header_ts
    for tid, times in trips.items():
        e = msg.entity.add()
        e.id = tid
        e.trip_update.trip.trip_id = tid
        if vehicles and tid in vehicles:
            e.trip_update.vehicle.id = vehicles[tid]
        for seq, t in sorted(times.items()):
            stu = e.trip_update.stop_time_update.add()
            stu.stop_sequence = seq
            stu.departure.time = t
    return msg.SerializeToString()


STOPPED_AT, IN_TRANSIT_TO = 1, 2


def vehicle_positions(vehicles: dict[str, tuple], header_ts=0) -> bytes:
    """vehicles: trip_id -> (vehicle_id, position timestamp[, stop_id, status])"""
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = header_ts
    for tid, (vid, ts, *where) in vehicles.items():
        e = msg.entity.add()
        e.id = vid
        e.vehicle.trip.trip_id = tid
        e.vehicle.vehicle.id = vid
        e.vehicle.timestamp = ts
        if where:
            e.vehicle.stop_id = where[0]
            e.vehicle.current_status = where[1] if len(where) > 1 else IN_TRANSIT_TO
    return msg.SerializeToString()
