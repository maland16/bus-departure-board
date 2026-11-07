"""Merging the schedule with realtime data into the list of upcoming departures at a stop.

For each scheduled visit, the first rule that applies sets the estimate:
  src=2  the realtime feed predicts this exact stop (matched on stop_sequence, since some
         trips visit a stop twice)
  src=1  the trip only has a prediction for its first stop: shift the schedule by that delay
  src=0  schedule only
A prediction is ignored (src=0, stale_vehicle) when the trip's vehicle hasn't reported its
position recently; those "ghost" buses show up as wildly late in the feed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .realtime import RealtimeSnapshot
from .static_gtfs import StaticIndex

SRC_SCHEDULE, SRC_ORIGIN, SRC_STOP = 0, 1, 2

# How late a bus can be and still be matched to its scheduled visit
MAX_LATENESS_S = 3 * 3600
# Realtime times further than this from the schedule are assumed to belong to another day's run
MAX_RT_OFFSET_S = 6 * 3600


@dataclass
class Departure:
    service_date: str
    trip_id: str
    stop_id: str
    stop_sequence: int
    route_id: str
    route: str
    route_color: str
    headsign: str
    ends_here: bool
    sched: int
    est: int
    src: int
    vehicle_id: str | None
    vehicle_age_s: int | None
    stale_vehicle: bool
    # Another trip on the same block has a stale vehicle: this trip's prediction may be
    # inherited from it. Flag only, to measure against UCOP before deciding what to do.
    stale_block: bool
    canceled: bool
    # Debugging: how many stop updates the trip had in the realtime feed (None = trip not in feed),
    # and the delay at its first stop
    rt_updates: int | None = None
    origin_delay_s: int | None = None
    timepoint: bool = True

    @property
    def delay_s(self) -> int:
        return self.est - self.sched

    def to_dict(self) -> dict:
        d = asdict(self)
        d["delay_s"] = self.delay_s
        return d


def compute_departures(
    index: StaticIndex,
    realtime: RealtimeSnapshot,
    stop_code: str,
    now: int,
    *,
    window_ahead_s: int,
    past_grace_s: int,
    stale_vehicle_s: int,
) -> list[Departure]:
    stop_id = index.stop_id_for(stop_code)
    if stop_id is None:
        return []

    stale_blocks = {
        index.trips[v.trip_id].block_id
        for v in realtime.vehicles_by_trip.values()
        if v.trip_id in index.trips and now - v.timestamp > stale_vehicle_s and index.trips[v.trip_id].block_id
    }

    out = []
    for sv in index.scheduled_between(stop_id, now - MAX_LATENESS_S, now + window_ahead_s):
        trip = sv.trip
        trt = realtime.trips.get(trip.trip_id)
        est, src = sv.sched, SRC_SCHEDULE
        origin_delay = None
        if trt:
            o = trt.stop_times.get(trip.origin_stop_sequence)
            origin_sched = index.origin_sched(trip, sv.service_date)
            if o and abs(o - origin_sched) < MAX_RT_OFFSET_S:
                origin_delay = o - origin_sched
            t = trt.stop_times.get(sv.visit.stop_sequence)
            if t and abs(t - sv.sched) < MAX_RT_OFFSET_S:
                est, src = t, SRC_STOP
            elif origin_delay is not None:
                est, src = sv.sched + origin_delay, SRC_ORIGIN

        vehicle = realtime.vehicles_by_trip.get(trip.trip_id)
        vehicle_age = int(now - vehicle.timestamp) if vehicle else None
        stale = vehicle_age is not None and vehicle_age > stale_vehicle_s
        if stale and src != SRC_SCHEDULE:
            est, src = sv.sched, SRC_SCHEDULE

        if est < now - past_grace_s or est > now + window_ahead_s:
            continue

        route = index.routes.get(trip.route_id)
        out.append(
            Departure(
                service_date=sv.service_date,
                trip_id=trip.trip_id,
                stop_id=stop_id,
                stop_sequence=sv.visit.stop_sequence,
                route_id=trip.route_id,
                route=route.short_name if route else trip.route_id,
                route_color=route.color if route else "",
                headsign=trip.headsign,
                ends_here=sv.ends_here,
                sched=sv.sched,
                est=est,
                src=src,
                vehicle_id=(vehicle.vehicle_id if vehicle else None) or (trt.vehicle_id if trt else None),
                vehicle_age_s=vehicle_age,
                stale_vehicle=stale,
                stale_block=bool(trip.block_id) and trip.block_id in stale_blocks and not vehicle,
                canceled=bool(trt and trt.canceled),
                rt_updates=len(trt.stop_times) if trt else None,
                origin_delay_s=origin_delay,
                timepoint=sv.visit.timepoint,
            )
        )
    out.sort(key=lambda d: (d.est, d.route))
    return out


def board_departures(departures: list[Departure], rows: int) -> list[Departure]:
    """What goes on the board: no canceled trips, and no trips that end at this stop."""
    return [d for d in departures if not d.canceled and not d.ends_here][:rows]
