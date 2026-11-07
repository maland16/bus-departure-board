"""Working out when buses actually left the watched stops, from successive VehiclePosition reports.

This is the ground truth for judging predictions (ours and UCOP's), and it works for any stop,
whether or not UCOP covers it.

GP Metro's vehicles report the stop they are at (STOPPED_AT) or heading to (IN_TRANSIT_TO).
Either way, every stop earlier in the trip has been left behind. So a watched stop at sequence s
was departed somewhere between the last report that still placed the bus at or before s and the
first report placing it after s. The server polls every 30 s; archived snapshots are 2 min apart,
so departures derived from the archive are coarser.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .realtime import VehicleRT
from .static_gtfs import StaticIndex

STOPPED_AT = 1


@dataclass(frozen=True)
class ObservedDeparture:
    service_date: str
    trip_id: str
    stop_id: str
    stop_sequence: int
    sched: int
    departed_after: int | None  # last report still at/before the stop (None: first seen already past it)
    departed_by: int  # first report past the stop
    vehicle_id: str
    seen_at_stop: bool  # a report had the bus STOPPED_AT this stop

    @property
    def actual(self) -> int:
        """Best estimate of the departure time: the middle of the window it happened in."""
        if self.departed_after is None:
            return self.departed_by
        return (self.departed_after + self.departed_by) // 2

    @property
    def uncertainty_s(self) -> int | None:
        return None if self.departed_after is None else (self.departed_by - self.departed_after) // 2


@dataclass
class DepartureObserver:
    stop_ids: set[str]
    # trip_id -> furthest stop_sequence the bus has been seen at or heading to
    _position: dict[str, int] = field(default_factory=dict)
    _last_ts: dict[str, int] = field(default_factory=dict)  # trip_id -> last report timestamp processed
    _before: dict[tuple[str, int], int] = field(default_factory=dict)  # (trip, seq) -> last report not yet past
    _seen_at: set[tuple[str, int]] = field(default_factory=set)
    _done: set[tuple[str, int]] = field(default_factory=set)
    _index_version: str | None = None
    _watched: dict[str, list[tuple[int, str]]] = field(default_factory=dict)  # trip -> [(seq, stop_id)]

    def _watched_visits(self, index: StaticIndex) -> dict[str, list[tuple[int, str]]]:
        if self._index_version != (index.version, id(index)):
            self._watched = {}
            for stop_id in self.stop_ids:
                for v in index.visits.get(stop_id, ()):
                    self._watched.setdefault(v.trip_id, []).append((v.stop_sequence, stop_id))
            self._index_version = (index.version, id(index))
        return self._watched

    def update(self, index: StaticIndex, vehicles_by_trip: dict[str, VehicleRT]) -> list[ObservedDeparture]:
        watched = self._watched_visits(index)
        out = []
        for trip_id, v in vehicles_by_trip.items():
            visits = watched.get(trip_id)
            if not visits or not v.stop_id or self._last_ts.get(trip_id) == v.timestamp:
                continue  # not a trip we watch, or nothing new from this bus
            self._last_ts[trip_id] = v.timestamp
            seqs = index.sequences_of_stop(trip_id, v.stop_id)
            prev = self._position.get(trip_id)
            later = [q for q in seqs if prev is None or q >= prev]
            if not later:
                continue  # reported stop isn't on this trip, or it went backwards (bad data)
            pos = later[0]
            self._position[trip_id] = pos
            for seq, stop_id in visits:
                key = (trip_id, seq)
                if key in self._done:
                    continue
                if seq < pos:
                    out.append(self._departure(index, trip_id, seq, stop_id, v))
                    self._done.add(key)
                else:
                    self._before[key] = v.timestamp
                    if seq == pos and v.status == STOPPED_AT:
                        self._seen_at.add(key)
        return [d for d in out if d is not None]

    def _departure(self, index, trip_id, seq, stop_id, v: VehicleRT) -> ObservedDeparture | None:
        key = (trip_id, seq)
        after = self._before.pop(key, None)
        seen = key in self._seen_at
        self._seen_at.discard(key)
        # Which day's run: the scheduled visit closest to when the bus left
        best = None
        for sv in index.scheduled_between(stop_id, v.timestamp - 4 * 3600, v.timestamp + 3600):
            if sv.trip.trip_id == trip_id and sv.visit.stop_sequence == seq:
                if best is None or abs(sv.sched - v.timestamp) < abs(best.sched - v.timestamp):
                    best = sv
        if best is None:
            return None
        return ObservedDeparture(best.service_date, trip_id, stop_id, seq, best.sched, after, v.timestamp,
                                 v.vehicle_id, seen)

    def forget_older_than(self, cutoff_ts: int) -> None:
        """Drop state for trips not heard from since cutoff_ts, so memory stays bounded."""
        stale = {t for t, ts in self._last_ts.items() if ts < cutoff_ts}
        for t in stale:
            self._last_ts.pop(t, None)
            self._position.pop(t, None)
        self._before = {k: ts for k, ts in self._before.items() if k[0] not in stale}
        self._seen_at = {k for k in self._seen_at if k[0] not in stale}
        self._done = {k for k in self._done if k[0] not in stale}
