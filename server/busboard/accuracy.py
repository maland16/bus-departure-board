"""How accurate are the predictions? Scores them against when buses actually left.

Replays archived realtime feeds through the current code, and works out the actual departures at
each stop. Those come from the server's live observations (data/busboard.db, 30 s resolution) where
available, otherwise from the archived vehicle positions (2 min resolution). Stops added to the config
later can be evaluated on older archives too, since predictions only need the feeds.

Pull the Pi's data first (data-pi/ is gitignored; don't use --delete into a copy you analyse):
    rsync -a busbox@192.168.1.27:busboard/data/ data-pi/

    uv run python -m busboard.accuracy data-pi
    uv run python -m busboard.accuracy data-pi --stop 586 --since 20261107-0600 --by src
    uv run python -m busboard.accuracy data-pi --config other.toml   # try different settings
    uv run python -m busboard.accuracy data-pi --rows acc.jsonl      # every scored prediction
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sqlite3
import statistics
from pathlib import Path

from .archive import InputArchive, replay_inputs
from .config import load_config
from .departures import compute_departures
from .drift import DriftModel
from .observe import DepartureObserver, ObservedDeparture

LEAD_BUCKETS = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 40), (40, 180)]  # minutes before departure


def load_observations(data_dir: Path) -> dict[tuple, ObservedDeparture]:
    out = {}
    db_path = data_dir / "busboard.db"
    if not db_path.exists():
        db_path = data_dir / "compare.db"  # a copy from before the rename
    if not db_path.exists():
        return out
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT service_date, trip_id, stop_id, stop_sequence, sched, departed_after, departed_by,"
                          " vehicle_id, seen_at_stop FROM observed_departures").fetchall()
    except sqlite3.OperationalError:
        return out  # older database without the table
    finally:
        db.close()
    for r in rows:
        o = ObservedDeparture(*r[:8], bool(r[8]))
        out[(o.service_date, o.trip_id, o.stop_sequence, o.stop_id)] = o
    return out


def bucket_of(lead_s: float) -> str | None:
    for lo, hi in LEAD_BUCKETS:
        if lo * 60 <= lead_s < hi * 60:
            return f"{lo}-{hi} min"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--stop", action="append")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--by", choices=["lead", "stop", "src", "route"], default="lead")
    ap.add_argument("--max-uncertainty", type=int, default=90,
                    help="skip departures whose time is known less precisely than this (s)")
    ap.add_argument("--rows", type=Path)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    archive = InputArchive(args.data_dir)
    live_obs = load_observations(args.data_dir)
    derived: dict[tuple, ObservedDeparture] = {}
    observer = None
    preds = []  # (ts, stop, key, est, extra)
    # Drift correction learns causally: only from departures observed before each snapshot
    drift = DriftModel(window_s=cfg.drift_window_s) if cfg.drift_correction else None
    live_sorted = sorted(live_obs.values(), key=lambda o: o.departed_by)
    live_i = 0

    for snap, index, rt, stops in replay_inputs(archive, cfg, args.since, args.until, args.stop):
        if observer is None:
            observer = DepartureObserver({index.stop_id_for(c) for c in cfg.stops if index.stop_id_for(c)})
        new = observer.update(index, rt.vehicles_by_trip)
        for o in new:
            derived[(o.service_date, o.trip_id, o.stop_sequence, o.stop_id)] = o
        observer.forget_older_than(snap.ts - 2 * 3600)
        if drift:
            while live_i < len(live_sorted) and live_sorted[live_i].departed_by <= snap.ts:
                drift.resolve(live_sorted[live_i])  # precise live observations first
                live_i += 1
            for o in new:
                drift.resolve(o)
            drift.prune(snap.ts)
        for code in stops:
            stop_id = index.stop_id_for(code)
            now = int(snap.ts)
            deps = compute_departures(index, rt, code, now, window_ahead_s=cfg.window_ahead_s,
                                      past_grace_s=cfg.past_grace_s, stale_vehicle_s=cfg.stale_vehicle_s,
                                      block_layover_s=cfg.block_layover_s)
            if drift:
                drift.record(index, deps, now)
                deps = drift.correct(index, [dataclasses.replace(d) for d in deps], now)
            for d in deps:
                if d.ends_here or d.canceled:
                    continue
                key = (d.service_date, d.trip_id, d.stop_sequence, stop_id)
                preds.append((now, code, key, d.est, {"src": d.src, "route": d.route, "sched": d.sched,
                                                      "held": d.held_at_timepoint, "stale": d.stale_vehicle,
                                                      "drift_s": d.drift_s}))

    obs = {**derived, **live_obs}  # live observations are more precise
    groups: dict[str, list[int]] = collections.defaultdict(list)
    out = args.rows.open("w") if args.rows else None
    scored = 0
    for now, code, key, est, extra in preds:
        o = obs.get(key)
        if o is None or o.departed_after is None or o.uncertainty_s > args.max_uncertainty:
            continue
        lead = o.actual - now
        b = bucket_of(lead)
        if b is None:
            continue  # predicted after the bus left, or too far ahead
        err = est - o.actual
        group = {"lead": b, "stop": code, "src": f"src{extra['src']}", "route": extra["route"]}[args.by]
        groups[group].append(err)
        scored += 1
        if out:
            out.write(json.dumps({"ts": now, "stop": code, "service_date": key[0], "trip_id": key[1],
                                  "stop_sequence": key[2], "est": est, "actual": o.actual,
                                  "uncertainty_s": o.uncertainty_s, "lead_s": lead, "error_s": err, **extra}) + "\n")
    if out:
        out.close()

    n_obs = sum(1 for o in obs.values() if o.departed_after is not None and o.uncertainty_s <= args.max_uncertainty)
    print(f"\n{len(obs)} observed departures ({len(live_obs)} live, {len(derived)} from the archive); "
          f"{n_obs} known to within {args.max_uncertainty} s. {scored} predictions scored.")
    print("error = predicted - actual departure (negative: the bus left later than predicted)\n")
    hdr = f"{args.by:>10} | {'n':>8} {'median':>7} {'MAE':>6} {'<=1m':>6} {'<=2m':>6}"
    print(hdr)
    print("-" * len(hdr))
    for g in sorted(groups, key=_sort_key):
        print(f"{g:>10} | {_stats(groups[g])}")
    return 0


def _sort_key(g: str):
    try:
        return (0, int(g.split("-")[0]), g)
    except ValueError:
        return (1, len(g), g)


def _stats(errs: list[int]) -> str:
    if not errs:
        return f"{0:>8} {'-':>7} {'-':>6} {'-':>6} {'-':>6}"
    n = len(errs)
    mae = sum(abs(e) for e in errs) / n
    w1 = sum(abs(e) <= 60 for e in errs) / n
    w2 = sum(abs(e) <= 120 for e in errs) / n
    return f"{n:>8} {statistics.median(errs) / 60:>+6.1f}m {mae / 60:>5.1f}m {w1:>6.0%} {w2:>6.0%}"


if __name__ == "__main__":
    raise SystemExit(main())
