"""Merging the schedule with realtime data into the list of upcoming departures at a stop.

For each scheduled visit, the first rule that applies sets the estimate:
  src=2  the realtime feed predicts this exact stop (matched on stop_sequence, since some
         trips visit a stop twice)
  src=1  no prediction for this stop: shift the schedule by the delay at the nearest stop that
         has one, preferring an earlier stop (usually the first stop: about half of the trips
         that haven't started only have a prediction for it)
  src=1  (also) no usable TripUpdate, but the trip's bus is reporting its position: the delay is
         its last report time minus its scheduled time at the stop it's at or heading to
         (from_vehicle). Seen: trips missing from TripUpdates for minutes while their bus ran 8 min
         late. If the bus is already past this stop, the departure is dropped.
  src=0  schedule only
With block propagation on, a trip without a prediction for this stop also can't start before
the bus finishes its previous trip on the same block: the estimate is at least the previous
trip's predicted end plus the minimum layover (block_delay_s; still src=1).
Then:
- A prediction is ignored (src=0, stale_vehicle) when the trip's vehicle hasn't reported its
  position recently; those "ghost" buses show up as wildly late in the feed.
- At timepoints the estimate is never earlier than the schedule (held_at_timepoint): buses
  wait there. Seen at Metro Pulse, where the feed predicted waiting buses leaving up to 19 min
  early and every one left on schedule. Not applied where the trip ends (an arrival).
- Waits at timepoints the bus hasn't reached yet also delay every stop after them (timepoint_wait_s).
  The feed doesn't model them: 2026-11-09 it had a route 8 bus 12 min early for a whole trip because it
  was sitting at its first stop before its start time; it then waited out those minutes at timepoints.
  Only earliness the feed carries all the way to the stop counts: 2026-11-10 route 5 buses parked before
  their start had the stops behind them at "now" but the rest of the trip on schedule (the feed already
  holds them).
- A trip that hasn't started can't be early anywhere. Buses are assigned to trips before they start
  and report stops on the way to the first one, and the feed extrapolates from those (seen: 22 min
  early), when in fact they start on time.
- A trip with no bus yet has its lateness capped (MAX_UNASSIGNED_DELAY_S): it's inherited from the
  bus's previous trips, and the feed lets it run away when that bus stops somewhere for a while
  while still reporting (2026-11-09: route 1 predicted 105 min late, then left 4 min late).
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
# A trip with no bus assigned yet: lateness inherited through the block is unreliable beyond this.
# Measured 2026-11-07..09, 2-40 min ahead: predicted +10-20 min -> actually +7; predicted 20+ -> +4.
MAX_UNASSIGNED_DELAY_S = 600


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
    ref_stop_sequence: int | None = None  # for src=1: the stop whose delay was applied
    held_at_timepoint: bool = False
    block_delay_s: int | None = None  # lateness carried over from the previous trip on the block
    from_vehicle: bool = False  # estimated from the bus's reported position (no TripUpdate)
    drift_s: int = 0  # correction applied by drift.DriftModel (learned from observed departures)
    timepoint_wait_s: int = 0  # added because the bus will wait at timepoints before this stop

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
    block_layover_s: int | None = None,
) -> list[Departure]:
    """block_layover_s: minimum time between a bus finishing a trip and starting its next one;
    None turns block propagation off."""
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
        origin_delay = ref_seq = None
        wait = 0
        base = sv.sched - sv.visit.departure_secs  # the service day's base, as epoch
        started = now >= base + trip.origin_departure_secs
        vehicle = realtime.vehicles_by_trip.get(trip.trip_id)
        if trt:
            o = trt.stop_times.get(trip.origin_stop_sequence)
            origin_sched = index.origin_sched(trip, sv.service_date)
            if o and abs(o - origin_sched) < MAX_RT_OFFSET_S:
                origin_delay = o - origin_sched
            t = trt.stop_times.get(sv.visit.stop_sequence)
            if t and abs(t - sv.sched) < MAX_RT_OFFSET_S:
                est, src = t, SRC_STOP
            else:
                ref = _nearest_delay(index, trip.trip_id, trt.stop_times, sv.visit.stop_sequence, base)
                if ref is not None:
                    ref_seq, delay = ref
                    est, src = sv.sched + delay, SRC_ORIGIN
            if src != SRC_SCHEDULE:
                wait = _timepoint_wait(index, trip.trip_id, trt.stop_times, sv.visit.stop_sequence, base, now, vehicle,
                                       started)
                est += wait
                if not started:
                    if not sv.ends_here and not sv.visit.timepoint:  # timepoints are held below
                        est = max(est, sv.sched)
                    if vehicle is None and not trt.vehicle_id:
                        est = min(est, sv.sched + MAX_UNASSIGNED_DELAY_S)

        block_delay = None
        if block_layover_s is not None and src != SRC_STOP:
            block_delay = _block_delay(index, realtime, trip, sv, now, stale_vehicle_s, block_layover_s)
            if block_delay is not None and sv.sched + block_delay > est:
                est, src = sv.sched + block_delay, SRC_ORIGIN

        vehicle_age = int(now - vehicle.timestamp) if vehicle else None
        stale = vehicle_age is not None and vehicle_age > stale_vehicle_s
        if stale and src != SRC_SCHEDULE:
            est, src = sv.sched, SRC_SCHEDULE

        from_vehicle = False
        if src == SRC_SCHEDULE and vehicle is not None and not stale and vehicle.stop_id:
            pos = _vehicle_position_delay(index, trip.trip_id, vehicle, sv)
            if pos == "passed":
                continue  # the bus has already left this stop
            if pos is not None:
                ref_seq, delay = pos
                est, src, from_vehicle = sv.sched + delay, SRC_ORIGIN, True

        held = sv.visit.timepoint and not sv.ends_here and est < sv.sched
        if held:
            est = sv.sched

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
                ref_stop_sequence=ref_seq,
                held_at_timepoint=held,
                block_delay_s=block_delay,
                from_vehicle=from_vehicle,
                timepoint_wait_s=wait if src != SRC_SCHEDULE else 0,
            )
        )
    out.sort(key=lambda d: (d.est, d.route))
    return out


def _nearest_delay(index: StaticIndex, trip_id: str, stop_times: dict[int, int], seq: int, base: int
                   ) -> tuple[int, int] | None:
    """(stop_sequence, delay) from the closest earlier stop with a prediction, else the closest later one."""
    before = [s for s in stop_times if s < seq]
    after = [s for s in stop_times if s > seq]
    for ref in ([max(before)] if before else []) + ([min(after)] if after else []):
        secs = index.stop_sched_secs(trip_id, ref)
        if secs is not None:
            delay = stop_times[ref] - (base + secs)
            if abs(delay) < MAX_RT_OFFSET_S:
                return ref, delay
    return None


def _timepoint_wait(index: StaticIndex, trip_id: str, stop_times: dict[int, int], seq: int, base: int, now: int,
                    vehicle, started: bool) -> int:
    """Seconds to add to a feed estimate for stop `seq`, for time the bus will spend waiting at timepoints
    ahead of it before that stop, by walking the feed's times up to it. Only for stops the bus hasn't
    reached: the feed keeps the times of stops already passed, and those were real departures.

    A timepoint's earliness only counts as far as the feed carries it, up to and including this stop:
    when the feed has the bus early at the timepoint but on schedule somewhere after it, the feed already
    holds it. Seen 2026-11-10 on route 5: buses parked before their start (at the first stop, or a couple
    of stops in) had the stops behind them at "now" and the rest of the trip on schedule; adding that
    earliness put them 13-18 min late everywhere."""
    timepoints = index.timepoints.get(trip_id, ())
    bus_at = None
    if started and vehicle is not None and vehicle.stop_id:
        at = [q for q in index.sequences_of_stop(trip_id, vehicle.stop_id) if q <= seq]
        bus_at = max(at) if at else None
    seqs = sorted(stop_times)
    wait = 0
    for i, q in enumerate(seqs):
        if q >= seq:
            break
        if q not in timepoints:
            continue
        if bus_at is not None:
            if q < bus_at:
                continue
        elif started and stop_times[q] < now:
            continue  # no position to go by: the feed's time says it's been and gone
        secs = index.stop_sched_secs(trip_id, q)
        if secs is None:
            continue
        early = base + secs - stop_times[q]
        for n in seqs[i + 1:]:
            if n > seq:
                break
            n_secs = index.stop_sched_secs(trip_id, n)
            if n_secs is not None:
                early = min(early, base + n_secs - stop_times[n])
        wait = max(wait, early)
    return wait


def _vehicle_position_delay(index: StaticIndex, trip_id: str, vehicle, sv):
    """(stop_sequence, delay) from where the bus says it is, or "passed" if it's beyond this stop."""
    seqs = index.sequences_of_stop(trip_id, vehicle.stop_id)
    if not seqs:
        return None
    at = [q for q in seqs if q <= sv.visit.stop_sequence]
    if not at:
        return "passed"
    vseq = max(at)
    secs = index.stop_sched_secs(trip_id, vseq)
    if secs is None:
        return None
    base = sv.sched - sv.visit.departure_secs
    # At or heading to stop vseq at this time, so at least this late there (IN_TRANSIT_TO means it
    # hasn't arrived yet). Running early before reaching this stop isn't trusted: buses wait.
    delay = vehicle.timestamp - (base + secs)
    if abs(delay) > MAX_RT_OFFSET_S:
        return None
    return vseq, max(delay, 0)


def _block_delay(index: StaticIndex, realtime: RealtimeSnapshot, trip, sv, now: int, stale_vehicle_s: int,
                 layover_s: int) -> int | None:
    """How late this trip will start because its bus is still finishing the previous trip on the block."""
    prev = index.previous_trip_on_block(trip)
    if prev is None:
        return None
    prt = realtime.trips.get(prev.trip_id)
    if not prt or not prt.stop_times:
        return None
    pveh = realtime.vehicles_by_trip.get(prev.trip_id)
    if pveh is not None and now - pveh.timestamp > stale_vehicle_s:
        return None  # a ghost bus's lateness isn't real
    base = sv.sched - sv.visit.departure_secs  # same service day for both trips
    last_seq = max(prt.stop_times)
    last_secs = index.stop_sched_secs(prev.trip_id, last_seq)
    end_secs = index.trip_end_secs(prev.trip_id)
    if last_secs is None or end_secs is None:
        return None
    prev_delay = prt.stop_times[last_seq] - (base + last_secs)
    if abs(prev_delay) > MAX_RT_OFFSET_S:
        return None
    prev_end = base + end_secs + prev_delay
    start_sched = base + trip.origin_departure_secs
    return max(0, prev_end + layover_s - start_sched)


def board_departures(departures: list[Departure], rows: int) -> list[Departure]:
    """What goes on the board: no canceled trips, and no trips that end at this stop."""
    return [d for d in departures if not d.canceled and not d.ends_here][:rows]
