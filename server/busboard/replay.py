"""Re-run archived comparisons through the current code.

Change the GTFS handling, then check whether agreement with UCOP got better or worse on
real recorded inputs, before deploying:

    rsync -a busbox@192.168.1.27:busboard/data/ data-pi/
    uv run python -m busboard.replay data-pi                      # summary per stop
    uv run python -m busboard.replay data-pi --stop 1117 --since 20261107-0600 --until 20261107-1000
    uv run python -m busboard.replay data-pi --rows rows.jsonl    # every comparison row, for jq/pandas
    uv run python -m busboard.replay data-pi --mismatches         # print each disagreement

Uses the thresholds in config.toml (or --config), so their effect can be tried too.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sys
from pathlib import Path

from .archive import InputArchive
from .compare import MATCH, OURS_ONLY, TIME_DIFF, UCOP_DEPARTED, UCOP_ONLY, compare_stop
from .config import load_config
from .realtime import snapshot_from_raw
from .static_gtfs import build_index
from .ucop import parse_stop_page


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path, help="a copy of the server's data/ directory")
    ap.add_argument("--config", type=Path, help="config.toml to take thresholds and stops from")
    ap.add_argument("--stop", action="append", help="only these stop codes (repeatable)")
    ap.add_argument("--since", help="YYYYMMDD[-HHMMSS]")
    ap.add_argument("--until", help="YYYYMMDD[-HHMMSS]")
    ap.add_argument("--rows", type=Path, help="write every comparison row as JSON lines")
    ap.add_argument("--mismatches", action="store_true", help="print each disagreement")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    archive = InputArchive(args.data_dir)
    stops_filter = set(args.stop or [])
    indexes: dict[str, object] = {}
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    out = args.rows.open("w") if args.rows else None
    n_snaps = 0

    for snap in archive.snapshots(args.since, args.until):
        static_name = snap.meta.get("static")
        if not static_name:
            print(f"skipping {snap.path}: no static version recorded", file=sys.stderr)
            continue
        stops = [s for s in snap.meta.get("stops", []) if not stops_filter or s in stops_filter]
        if not stops:
            continue
        key = (static_name, tuple(sorted(set(cfg.stops) | set(stops))))
        if key not in indexes:
            zip_path = archive.static_dir / static_name
            if not zip_path.exists():
                print(f"skipping {snap.path}: static version {static_name} not archived", file=sys.stderr)
                continue
            print(f"building index for {static_name}...", file=sys.stderr)
            indexes[key] = build_index(zip_path.read_bytes(), list(key[1]), snap.meta.get("static_version", ""))
        index = indexes[key]
        rt = snapshot_from_raw(snap.read("TripUpdate.pb"), snap.read("VehiclePosition.pb"),
                               snap.meta.get("trip_updates_fetched_at", 0))
        n_snaps += 1
        for code in stops:
            html = snap.ucop_html(code)
            ucop_meta = snap.meta.get("ucop", {}).get(code, {})
            if html is None or ucop_meta.get("error"):
                counts[code]["ucop_error"] += 1
                continue
            now = int(ucop_meta.get("fetched_at") or snap.ts)
            arrivals = parse_stop_page(html, now, index.tz)
            _, rows = compare_stop(index, rt, arrivals, code, now, cfg)
            for r in rows:
                c = counts[code]
                c[r.category] += 1
                if r.category == TIME_DIFF:
                    c["diff_stale_vehicle" if r.stale_vehicle else "diff_stale_block" if r.stale_block
                      else f"diff_src{r.our_src}"] += 1
                if r.category in (MATCH, TIME_DIFF):
                    c[f"src{r.our_src}"] += 1
                if r.headsign_differs:
                    c["headsign_differs"] += 1
                if out:
                    rec = dataclasses.asdict(r)
                    rec.update(ts=now, stop=code, diff_s=r.diff_s, headsign_differs=r.headsign_differs,
                               archive=str(snap.path))
                    out.write(json.dumps(rec) + "\n")
                if args.mismatches and r.category in (TIME_DIFF, OURS_ONLY, UCOP_ONLY):
                    print(f"{snap.path.parent.name}-{snap.path.name} stop {code} {r.category:9} route {r.route:>4} "
                          f"{r.trip_id} src={r.our_src} diff={r.diff_s} veh_age={r.vehicle_age_s} "
                          f"rt_updates={r.rt_updates} origin_delay={r.origin_delay_s} tp={int(bool(r.timepoint))} "
                          f"stale={'V' if r.stale_vehicle else ''}{'B' if r.stale_block else ''} "
                          f"ucop={r.ucop_status_text!r}")
    if out:
        out.close()

    print(f"\n{n_snaps} snapshots replayed\n")
    hdr = f"{'stop':>5} {'rows':>6} {'both':>6} {'match':>6} {'stale V':>7} {'stale B':>7} {'diff s2/s1/s0':>14} {'ours/ucop only':>15} {'headsign':>8}"
    print(hdr)
    print("-" * len(hdr))
    total = collections.Counter()
    for code in sorted(counts, key=lambda c: (len(c), c)):
        c = counts[code]
        total.update(c)
        print(_line(code, c))
    if len(counts) > 1:
        print("-" * len(hdr))
        print(_line("all", total))
    return 0


def _line(code, c) -> str:
    both = c[MATCH] + c[TIME_DIFF]
    rows = both + c[OURS_ONLY] + c[UCOP_ONLY]
    pct = lambda n, d: f"{100 * n / d:5.1f}%" if d else "    -"
    return (f"{code:>5} {rows:>6} {pct(both, rows):>6} {pct(c[MATCH], both):>6} {c['diff_stale_vehicle']:>7} "
            f"{c['diff_stale_block']:>7} {c['diff_src2']:>4}/{c['diff_src1']}/{c['diff_src0']:<5} "
            f"{c[OURS_ONLY]:>7}/{c[UCOP_ONLY]:<7} {c['headsign_differs']:>8}")


if __name__ == "__main__":
    raise SystemExit(main())
