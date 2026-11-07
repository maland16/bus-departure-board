"""Learning how far buses drift from the predictions, per route and direction, from observed departures.

Measured on 2026-11-07: GP Metro's predictions are right just before a bus leaves, but get
steadily worse with lead time, in a direction that depends on the route. Buses on routes 7, 8,
9A and 9B left ~2-3 min later than predicted 20+ min ahead; on routes 4, 21 and 24A, 2-4 min
earlier. The feed seems to assume a bus keeps its current delay, when it actually keeps losing
(or gaining) time along the route.

So for each (route, direction, stop) this fits a single rate r from the last few hours of departures
seen in vehicle positions:  actual = predicted + r * lead.  It's applied to predictions as
predicted + r * lead, shrunk toward 0 until enough departures have been seen. Because it only
uses departures that already happened, it adapts to traffic during the day (and weekday vs
weekend) instead of baking in a fixed bias.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from .departures import SRC_ORIGIN, SRC_STOP, Departure
from .static_gtfs import StaticIndex

MIN_LEAD_S = 120  # predictions made closer than this barely drift; skip them
MAX_LEAD_S = 3600  # the board horizon; longer leads would dominate the fit


@dataclass
class DriftModel:
    window_s: int = 3 * 3600  # learn from departures in the last few hours
    shrink_departures: float = 3.0  # rate is scaled by n / (n + this), n = departures seen
    max_rate: float = 0.25  # at most 15 s of correction per minute of lead
    max_shift_s: int = 300  # and never more than 5 min in total
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
        if not preds or obs.departed_after is None:
            return  # without a lower bound the departure time isn't reliable
        actual = obs.actual
        dep = (obs.service_date, obs.trip_id, obs.stop_sequence, obs.stop_id)
        for made_at, est, key in preds:
            lead = actual - made_at
            if MIN_LEAD_S <= lead <= MAX_LEAD_S:
                self._samples[key].append((actual, lead, est - actual, dep))

    def warm_start(self, index: StaticIndex, rows: list[dict]) -> int:
        """Rebuild samples from stored predictions + observed departures (CompareStore.drift_training),
        so a restart doesn't lose hours of learning. Returns how many samples were added."""
        added = 0
        for r in rows:
            stop_id = index.stop_id_for(r["stop_code"])
            key = self.key_of(index, r["trip_id"], stop_id) if stop_id else None
            lead = r["actual"] - r["ts"]
            if key and MIN_LEAD_S <= lead <= MAX_LEAD_S:
                dep = (r["service_date"], r["trip_id"], r["stop_sequence"], stop_id)
                self._samples[key].append((r["actual"], lead, r["raw_est"] - r["actual"], dep))
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

    def rate(self, key: tuple, now: int) -> tuple[float, int]:
        """(rate, departures it's based on): seconds of drift per second of lead."""
        q = self._samples.get(key)
        if not q:
            return 0.0, 0
        cutoff = now - self.window_s
        num = den = 0.0
        deps = set()
        for actual, lead, err, dep in q:
            if actual >= cutoff:
                num += -err * lead
                den += lead * lead
                deps.add(dep)
        if not den:
            return 0.0, 0
        n = len(deps)
        r = num / den * n / (n + self.shrink_departures)
        return max(-self.max_rate, min(self.max_rate, r)), n

    def correct(self, index: StaticIndex, departures: list[Departure], now: int) -> list[Departure]:
        """Shift realtime-based estimates by the learned drift (in place), keeping timepoint holds."""
        rates: dict[tuple, float] = {}
        for d in departures:
            lead = d.est - now
            if d.src not in (SRC_ORIGIN, SRC_STOP) or d.ends_here or lead <= 0:
                continue
            key = self.key_of(index, d.trip_id, d.stop_id)
            if key is None:
                continue
            if key not in rates:
                rates[key] = self.rate(key, now)[0]
            shift = max(-self.max_shift_s, min(self.max_shift_s, round(rates[key] * min(lead, MAX_LEAD_S))))
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
            r, n = self.rate(key, now)
            if n:
                route = index.routes.get(key[0])
                out[f"{route.short_name if route else key[0]}/{key[1] or '-'}@{key[2]}"] = {
                    "rate": round(r, 3), "departures": n, "shift_at_20min_s": round(r * 1200)}
        return dict(sorted(out.items()))
