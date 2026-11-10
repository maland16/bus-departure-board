"""Learning how far buses differ from the predictions, per route, direction and stop, from observed departures.

Measured on 2026-11-07..09: buses leave a steady ~1 min later than GP Metro's predictions, whether
those are made 3 minutes or 40 minutes ahead, by an amount that depends on the route and place.
(At 2-40 min ahead the median error was -0.8 to -1.2 min, flat with lead; 0-2 min it's -0.3.)

So for each (route, direction, stop) this learns a single offset from the last few hours of
departures seen in vehicle positions: actual = predicted + offset. It's shrunk toward 0 until enough
departures have been seen. Because it only uses departures that already happened, it adapts to traffic
during the day (and weekday vs weekend) instead of baking in a fixed bias.

A correction that grows with lead (rate * lead, used until 2026-11-09) fitted worse: MAE 10-20 min
ahead 2.16 vs 2.01 min, with the buses it left uncorrected at short range still ~1 min off.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from .departures import SRC_ORIGIN, SRC_STOP, Departure
from .static_gtfs import StaticIndex

MIN_LEAD_S = 120  # predictions made closer than this barely drift; skip them
MAX_LEAD_S = 3600  # the board horizon
# The feed is good in the last minute or so: apply no correction below RAMP_START_S, all of it from RAMP_FULL_S
RAMP_START_S = 60
RAMP_FULL_S = 180
# Errors this large are a feed glitch, not something to learn (2026-11-09: a bus idling for 2.5 h
# had 100 min of lateness inherited through its block and would otherwise have dragged the mean)
MAX_ERROR_S = 600
MAX_OBS_UNCERTAINTY_S = 90  # departure-time window (+/-) we trust enough to learn from


@dataclass
class DriftModel:
    window_s: int = 3 * 3600  # learn from departures in the last few hours
    shrink_departures: float = 3.0  # offset is scaled by n / (n + this), n = departures seen
    max_shift_s: int = 300  # never more than 5 min
    # (service_date, trip_id, stop_sequence, stop_id) -> [(made_at, raw_est, key)]
    _pending: dict[tuple, list[tuple[int, int, tuple]]] = field(default_factory=dict)
    # (route_id, direction_id, stop_id) -> deque of (actual, lead_s, error_s, departure_key)
    _samples: dict[tuple, collections.deque] = field(default_factory=lambda: collections.defaultdict(collections.deque))

    @staticmethod
    def key_of(index: StaticIndex, trip_id: str, stop_id: str) -> tuple | None:
        # Per stop, not just per route: the same route drifts differently along its length
        # (2026-11-07: ~3 min at Congress St + Park/State, ~0 at Congress St + Franklin)
        t = index.trips.get(trip_id)
        return (t.route_id, t.direction_id, stop_id) if t else None

    def record(self, index: StaticIndex, departures: list[Departure], now: int) -> None:
        """Remember raw (uncorrected) predictions, to score once the bus is seen leaving."""
        for d in departures:
            lead = d.est - now
            if d.src not in (SRC_ORIGIN, SRC_STOP) or d.ends_here or not (MIN_LEAD_S <= lead <= MAX_LEAD_S):
                continue
            key = self.key_of(index, d.trip_id, d.stop_id)
            if key:
                self._pending.setdefault((d.service_date, d.trip_id, d.stop_sequence, d.stop_id), []).append(
                    (now, d.est, key))

    def resolve(self, obs) -> None:
        """An observed departure (observe.ObservedDeparture): turn its pending predictions into samples."""
        preds = self._pending.pop((obs.service_date, obs.trip_id, obs.stop_sequence, obs.stop_id), None)
        if not preds or obs.departed_after is None or obs.uncertainty_s > MAX_OBS_UNCERTAINTY_S:
            return  # the departure time isn't known well enough
        actual = obs.actual
        dep = (obs.service_date, obs.trip_id, obs.stop_sequence, obs.stop_id)
        for made_at, est, key in preds:
            lead = actual - made_at
            if MIN_LEAD_S <= lead <= MAX_LEAD_S and abs(est - actual) <= MAX_ERROR_S:
                self._samples[key].append((actual, lead, est - actual, dep))

    def warm_start(self, index: StaticIndex, rows: list[dict]) -> int:
        """Rebuild samples from stored predictions + observed departures (CompareStore.drift_training),
        so a restart doesn't lose hours of learning. Returns how many samples were added."""
        added = 0
        for r in rows:
            stop_id = index.stop_id_for(r["stop_code"])
            key = self.key_of(index, r["trip_id"], stop_id) if stop_id else None
            lead = r["actual"] - r["ts"]
            err = r["raw_est"] - r["actual"]
            if key and MIN_LEAD_S <= lead <= MAX_LEAD_S and abs(err) <= MAX_ERROR_S:
                dep = (r["service_date"], r["trip_id"], r["stop_sequence"], stop_id)
                self._samples[key].append((r["actual"], lead, err, dep))
                added += 1
        for q in self._samples.values():  # keep each queue in time order for prune()
            ordered = sorted(q, key=lambda s: s[0])
            q.clear()
            q.extend(ordered)
        return added

    def prune(self, now: int) -> None:
        cutoff = now - self.window_s
        for q in self._samples.values():
            while q and q[0][0] < cutoff:
                q.popleft()
        # Predictions for departures never observed (bus not seen, or canceled)
        self._pending = {k: v for k, v in self._pending.items() if v[-1][0] >= cutoff}

    def offset(self, key: tuple, now: int) -> tuple[float, int]:
        """(offset in seconds, departures it's based on): how much later than predicted buses leave here."""
        q = self._samples.get(key)
        if not q:
            return 0.0, 0
        cutoff = now - self.window_s
        total = count = 0
        deps = set()
        for actual, _lead, err, dep in q:
            if actual >= cutoff:
                total -= err
                count += 1
                deps.add(dep)
        if not count:
            return 0.0, 0
        n = len(deps)
        return max(-self.max_shift_s, min(self.max_shift_s, total / count * n / (n + self.shrink_departures))), n

    def correct(self, index: StaticIndex, departures: list[Departure], now: int) -> list[Departure]:
        """Shift realtime-based estimates by the learned offset (in place), keeping timepoint holds."""
        offsets: dict[tuple, float] = {}
        for d in departures:
            lead = d.est - now
            if d.src not in (SRC_ORIGIN, SRC_STOP) or d.ends_here or lead <= 0:
                continue
            key = self.key_of(index, d.trip_id, d.stop_id)
            if key is None:
                continue
            if key not in offsets:
                offsets[key] = self.offset(key, now)[0]
            shift = round(offsets[key] * max(0.0, min(1.0, (lead - RAMP_START_S) / (RAMP_FULL_S - RAMP_START_S))))
            if shift:
                d.est += shift
                d.drift_s = shift
                if d.timepoint and d.est < d.sched:  # buses wait at timepoints
                    d.drift_s += d.sched - d.est
                    d.est = d.sched
                    d.held_at_timepoint = True
        departures.sort(key=lambda d: (d.est, d.route))
        return departures

    def status(self, index: StaticIndex, now: int) -> dict:
        out = {}
        for key in self._samples:
            offset, n = self.offset(key, now)
            if n:
                route = index.routes.get(key[0])
                out[f"{route.short_name if route else key[0]}/{key[1] or '-'}@{key[2]}"] = {
                    "offset_s": round(offset), "departures": n}
        return dict(sorted(out.items()))
