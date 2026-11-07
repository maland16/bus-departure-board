"""Polling GP Metro's GTFS-realtime TripUpdate and VehiclePosition feeds.

This feed gives absolute times (never `delay`), no start_date, and keeps updates for stops a
bus has already passed. Many not-yet-started trips only have an update for their first stop.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx
from google.transit import gtfs_realtime_pb2 as rt

from .static_gtfs import USER_AGENT

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TripRT:
    trip_id: str
    stop_times: dict[int, int]  # stop_sequence -> epoch (departure, else arrival)
    vehicle_id: str | None
    canceled: bool = False


@dataclass(frozen=True)
class VehicleRT:
    vehicle_id: str
    trip_id: str
    timestamp: int  # when the vehicle last reported its position
    stop_id: str
    # GTFS-RT VehicleStopStatus for stop_id: 0 INCOMING_AT, 1 STOPPED_AT, 2 IN_TRANSIT_TO
    status: int = 2


@dataclass
class RealtimeSnapshot:
    trips: dict[str, TripRT] = field(default_factory=dict)
    vehicles_by_trip: dict[str, VehicleRT] = field(default_factory=dict)
    trip_updates_header_ts: int = 0
    vehicle_positions_header_ts: int = 0
    trip_updates_fetched_at: float = 0
    vehicle_positions_fetched_at: float = 0
    # Raw feed bytes, archived with each comparison so it can be replayed
    trip_updates_raw: bytes | None = field(default=None, repr=False)
    vehicle_positions_raw: bytes | None = field(default=None, repr=False)


def parse_trip_updates(data: bytes) -> tuple[int, dict[str, TripRT]]:
    msg = rt.FeedMessage()
    msg.ParseFromString(data)
    trips = {}
    canceled = rt.TripDescriptor.ScheduleRelationship.Value("CANCELED")
    skipped = rt.TripUpdate.StopTimeUpdate.ScheduleRelationship.Value("SKIPPED")
    for e in msg.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        times = {}
        for stu in tu.stop_time_update:
            if stu.schedule_relationship == skipped or not stu.HasField("stop_sequence"):
                continue
            ev = stu.departure if stu.HasField("departure") and stu.departure.time else stu.arrival
            if ev.time:
                times[stu.stop_sequence] = ev.time
        trips[tu.trip.trip_id] = TripRT(
            trip_id=tu.trip.trip_id,
            stop_times=times,
            vehicle_id=tu.vehicle.id or None,
            canceled=tu.trip.schedule_relationship == canceled,
        )
    return msg.header.timestamp, trips


def parse_vehicle_positions(data: bytes) -> tuple[int, dict[str, VehicleRT]]:
    msg = rt.FeedMessage()
    msg.ParseFromString(data)
    out = {}
    for e in msg.entity:
        if not e.HasField("vehicle"):
            continue
        v = e.vehicle
        if not v.trip.trip_id:
            continue
        out[v.trip.trip_id] = VehicleRT(
            vehicle_id=v.vehicle.id or e.id,
            trip_id=v.trip.trip_id,
            timestamp=v.timestamp or msg.header.timestamp,
            stop_id=v.stop_id,
            status=v.current_status,
        )
    return msg.header.timestamp, out


def carry_over_missing_trips(
    new_trips: dict[str, TripRT], last_seen: dict[str, tuple[TripRT, float]], now: float, hold_s: float
) -> dict[str, TripRT]:
    """Trips occasionally vanish from one TripUpdate fetch and come back on the next. Keep a trip
    that was seen within hold_s, rather than dropping it to the schedule for a poll or two.
    Updates `last_seen` in place."""
    merged = dict(new_trips)
    for tid, (trt, seen_at) in list(last_seen.items()):
        if tid in new_trips:
            continue
        if now - seen_at <= hold_s:
            merged[tid] = trt
        else:
            del last_seen[tid]
    for tid, trt in new_trips.items():
        last_seen[tid] = (trt, now)
    return merged


class RealtimeFeed:
    def __init__(self, trip_updates_url: str, vehicle_positions_url: str, hold_missing_trips_s: float = 0):
        self.trip_updates_url = trip_updates_url
        self.vehicle_positions_url = vehicle_positions_url
        self.hold_missing_trips_s = hold_missing_trips_s
        self._last_seen: dict[str, tuple[TripRT, float]] = {}
        self.snapshot = RealtimeSnapshot()
        self.last_error: str | None = None
        self._client = httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})

    def refresh(self) -> None:
        """Fetch both feeds. A feed that fails keeps its previous data (its age shows in /health)."""
        snap = self.snapshot
        errors = []
        new = RealtimeSnapshot(**snap.__dict__)
        try:
            resp = self._client.get(self.trip_updates_url)
            resp.raise_for_status()
            new.trip_updates_header_ts, trips = parse_trip_updates(resp.content)
            new.trip_updates_fetched_at = time.time()
            new.trips = carry_over_missing_trips(trips, self._last_seen, new.trip_updates_fetched_at,
                                                 self.hold_missing_trips_s)
            new.trip_updates_raw = resp.content
        except Exception as e:
            errors.append(f"TripUpdate: {type(e).__name__}: {e}")
        try:
            resp = self._client.get(self.vehicle_positions_url)
            resp.raise_for_status()
            new.vehicle_positions_header_ts, new.vehicles_by_trip = parse_vehicle_positions(resp.content)
            new.vehicle_positions_fetched_at = time.time()
            new.vehicle_positions_raw = resp.content
        except Exception as e:
            errors.append(f"VehiclePosition: {type(e).__name__}: {e}")
        self.snapshot = new
        self.last_error = "; ".join(errors) or None
        if errors:
            log.warning("realtime refresh: %s", self.last_error)


def snapshot_from_raw(trip_updates: bytes | None, vehicle_positions: bytes | None, fetched_at: float = 0) -> RealtimeSnapshot:
    """Rebuild a snapshot from archived feed bytes (for replay)."""
    snap = RealtimeSnapshot(trip_updates_raw=trip_updates, vehicle_positions_raw=vehicle_positions)
    if trip_updates:
        snap.trip_updates_header_ts, snap.trips = parse_trip_updates(trip_updates)
        snap.trip_updates_fetched_at = fetched_at
    if vehicle_positions:
        snap.vehicle_positions_header_ts, snap.vehicles_by_trip = parse_vehicle_positions(vehicle_positions)
        snap.vehicle_positions_fetched_at = fetched_at
    return snap
