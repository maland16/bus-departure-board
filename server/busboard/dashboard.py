"""The status page at /: is the server doing its job (publishing fresh departures), and is it logging?

Rendered on the server from in-memory state; the database and disk figures are cached for a minute
so a page left open (it refreshes every 30 s) costs the Pi next to nothing.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import shutil
import threading
import time
from pathlib import Path

STATS_TTL_S = 60
EVENTS_SHOWN = 25
_stats_lock = threading.Lock()
_stats: dict = {"at": 0.0}


def invalidate() -> None:
    _stats["at"] = 0.0


def _cached_stats(a, now: float) -> dict:
    with _stats_lock:
        if now - _stats["at"] < STATS_TTL_S:
            return _stats
        since = int(now) - 86400
        _stats.update(
            at=now,
            accuracy=a.store.accuracy(since),
            counts=a.store.counts_since(since),
            db_bytes=a.store.size_bytes(),
            disk=shutil.disk_usage(a.cfg.data_dir) if a.cfg.data_dir.exists() else None,
            drift=_drift_by_stop(a, int(now)),
            events=_recent_events(a.events.dir, EVENTS_SHOWN),
        )
        return _stats


def _drift_by_stop(a, now: int) -> dict[str, list[int]]:
    if not a.drift or a.static.index is None:
        return {}
    out: dict[str, list[int]] = {}
    for key, v in a.drift.status(a.static.index, now).items():
        out.setdefault(key.rsplit("@", 1)[-1], []).append(v["offset_s"])
    return out


def _recent_events(log_dir: Path, n: int) -> list[dict]:
    out: list[dict] = []
    for path in sorted(log_dir.glob("events-*.jsonl"), reverse=True)[:3]:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
            if len(out) >= n:
                return out
    return out


# ---- formatting

def _e(x) -> str:
    return html.escape(str(x))


def _ago(age_s: float | None) -> str:
    if age_s is None:
        return "never"
    age_s = int(age_s)
    if age_s < 120:
        return f"{age_s} s ago"
    if age_s < 7200:
        return f"{age_s // 60} min ago"
    if age_s < 172800:
        return f"{age_s // 3600} h ago"
    return f"{age_s // 86400} days ago"


def _dur(s: float) -> str:
    s = int(s)
    return f"{s // 86400} d {s % 86400 // 3600} h" if s >= 86400 else f"{s // 3600} h {s % 3600 // 60} min"


def _bytes(n: int | None) -> str:
    if n is None:
        return "–"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def _rss() -> str:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return _bytes(int(line.split()[1]) * 1024)
    except OSError:
        pass
    return "–"


def _dl(items: list[tuple[str, str]]) -> str:
    return "<dl>" + "".join(f"<dt>{_e(k)}</dt><dd>{v}</dd>" for k, v in items) + "</dl>"


def _flag(ok: bool, text: str) -> str:
    return f'<span class="{"good" if ok else "bad"}">{_e(text)}</span>'


SRC_MARK = {2: "", 1: "~", 0: "s"}  # as on the board's legend: ~ estimated, s schedule only


def _next_deps(payload: dict | None, now: int) -> str:
    if not payload:
        return '<span class="muted">–</span>'
    parts = []
    for d in payload["deps"][:3]:
        mins = max(0, (d["t"] - now) // 60)
        parts.append(f'<span class="dep"><b>{_e(d["r"])}</b> {mins}′{SRC_MARK.get(d["src"], "")}</span>')
    return " ".join(parts) or '<span class="muted">none in 3 h</span>'


# ---- page

def render(a) -> str:
    now = time.time()
    st = _cached_stats(a, now)
    idx = a.static.index
    snap = a.realtime.snapshot
    problems = a.problems(now)
    matched, total = a.trip_match_rate()

    banner = ('<div class="banner ok"><b>All good.</b> Departures are being published and logged.</div>'
              if not problems else
              '<div class="banner warn"><b>Needs attention</b><ul>'
              + "".join(f"<li>{_e(p)}</li>" for p in problems) + "</ul></div>")

    # Publishing
    if a.PUBLISHING:
        ps = a.publisher.status()
        served = ("–" if ps["verify_age_s"] is None else
                  _flag(ps["served_lag_s"] is not None and ps["served_lag_s"] <= a.SERVED_STALE_S,
                        f'{ps["served_lag_s"]} s old' if ps["served_lag_s"] is not None else "check failed")
                  + f' <span class="muted">(checked {_ago(ps["verify_age_s"])})</span>')
        publishing = _dl([
            ("Last push", _flag(ps["error"] is None and ps["last_ok_age_s"] is not None, _ago(ps["last_ok_age_s"]))
             + (f' <span class="muted">({ps["last_push_ms"]} ms)</span>' if ps["last_push_ms"] is not None else "")),
            ("Pushes since start", _e(ps["pushes"])),
            ("Web host serves data", served),
            ("Last error", _e(ps["error"] or ps["verify_error"] or "none")),
            ("Public URL", f'<a href="{_e(ps["public_url"])}/index.json">{_e(ps["public_url"])}</a>'),
        ])
    else:
        publishing = '<p class="bad">Off. Set <code>[publish] enabled</code> and <code>target</code> in config.toml.</p>'

    # Feeds
    cyc = a.cycle_status
    feeds = _dl([
        ("TripUpdates", _flag(a.realtime_is_live(now), _ago(now - snap.trip_updates_fetched_at
                                                           if snap.trip_updates_fetched_at else None))
         + (f' <span class="muted">(feed {int(now - snap.trip_updates_header_ts)} s old)</span>'
            if snap.trip_updates_header_ts else "")),
        ("VehiclePositions", _ago(now - snap.vehicle_positions_fetched_at if snap.vehicle_positions_fetched_at else None)
         + f' <span class="muted">({len(snap.vehicles_by_trip)} buses)</span>'),
        ("Trips matched to schedule", _flag(total < 20 or matched / total >= 0.9, f"{matched}/{total}")),
        ("Realtime error", _e(a.realtime.last_error or "none")),
        ("Static schedule", _e(idx.version if idx else "not loaded")
         + (f' <span class="muted">({_e(idx.first_date)}–{_e(idx.last_date)}, {len(idx.trips)} trips)</span>'
            if idx else "")),
        ("Static checked", _ago(now - a.static.last_check if a.static.last_check else None)
         + (f' <span class="bad">{_e(a.static.last_error)}</span>' if a.static.last_error else "")),
        ("Update cycle", (_ago(now - cyc["at"]) if cyc["at"] else "not yet")
         + (f' <span class="muted">(took {cyc["took_ms"]} ms)</span>' if cyc["took_ms"] is not None else "")
         + (f' <span class="bad">{_e(cyc["error"])}</span>' if cyc["error"] else "")),
    ])

    # Logging
    disk = st["disk"]
    days = a.archive.days()
    logging_ = _dl([
        ("Last logged", _ago(now - a._last_logged if a._last_logged else None)
         + (f' <span class="muted">({cyc["logged_rows"]} predictions, every {a.cfg.log_interval_s} s)</span>'
            if cyc["logged_rows"] is not None else "")),
        ("Archive", f'{_bytes(a.archive.size_bytes)} <span class="muted">({len(days)} days'
         + (f", since {days[0][:4]}-{days[0][4:6]}-{days[0][6:]}" if days else "")
         + f"; kept {a.cfg.archive_days})</span>"),
        ("Database", f'{_bytes(st["db_bytes"])} <span class="muted">(kept {a.cfg.retention_days} days)</span>'),
        ("Last 24 h", f'{sum(c["predictions"] for c in st["counts"].values()):,} predictions, '
                      f'{sum(c["observed"] for c in st["counts"].values()):,} departures observed'),
        ("Disk free", "–" if disk is None else _flag(disk.free >= a.LOW_DISK_BYTES,
                                                     f"{_bytes(disk.free)} of {_bytes(disk.total)}")),
    ])

    # Stops
    rows = []
    for code in a.cfg.stops:
        stop_id = idx.stop_id_for(code) if idx else None
        if idx and stop_id is None:
            rows.append(f'<tr><td>{_e(code)}</td><td colspan="6" class="bad">Not in the current static feed</td></tr>')
            continue
        p = a._payloads.get(code)
        srcs = [d["src"] for d in p["deps"]] if p else []
        acc = st["accuracy"].get(code)
        counts = st["counts"].get(code, {"predictions": 0, "observed": 0})
        offsets = st["drift"].get(stop_id or "", [])
        drift_cell = (f"{len(offsets)} keys, max {max(offsets, key=abs) / 60:+.1f} min" if offsets else "–")
        acc_cell = (f'{acc["mae"] / 60:.1f} min <span class="muted">({acc["n"]}; {acc["w2"] * 100:.0f}% ≤2 min)</span>'
                    if acc and acc["n"] else "–")
        rows.append(
            f'<tr><td><a href="/stops/{_e(code)}/departures?debug=1">{_e(code)}</a></td>'
            f'<td class="name">{_e(p["name"] if p else (a._stop_name(idx, code) if idx else ""))}</td>'
            f"<td>{_next_deps(p, int(now))}</td>"
            f'<td class="num">{srcs.count(2)}/{srcs.count(1)}/{srcs.count(0)}</td>'
            f'<td class="num">{counts["observed"]}</td><td>{acc_cell}</td><td>{drift_cell}</td></tr>')
    stops = ('<div class="wrap"><table><thead><tr><th>Stop</th><th>Name</th><th>Next (published)</th>'
             '<th title="rows in the payload: predicted for the stop / estimated / schedule only">Src 2/1/0</th>'
             '<th>Left, 24 h</th><th title="mean absolute error of logged predictions made 5-20 min before the '
             'observed departure, last 24 h">Error 5–20 min ahead</th><th>Drift correction</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')

    # Events
    ev_rows = []
    for ev in st["events"]:
        name = ev.get("event", "")
        cls = "bad" if name.endswith(("error", "dip", "stale")) else "good" if name.endswith("recovered") else ""
        detail = {k: v for k, v in ev.items() if k not in ("ts", "time", "event")}
        text = "; ".join(f"{k}={v}" for k, v in detail.items() if k not in ("visits", "unmatched_sample"))
        when = dt.datetime.fromtimestamp(ev.get("ts", 0)).strftime("%a %H:%M:%S")
        ev_rows.append(f'<tr><td class="when">{when}</td><td class="{cls}">{_e(name)}</td>'
                       f'<td class="detail">{_e(text[:300])}</td></tr>')
    events = (f'<div class="wrap"><table class="events"><tbody>{"".join(ev_rows)}</tbody></table></div>'
              if ev_rows else '<p class="muted">No events yet.</p>')

    generated = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Busboard status</title>{STYLE}</head><body>
<header><h1>Busboard server</h1><span class="muted">{generated} · up {_dur(now - a.started_at)} · {_rss()} RSS</span></header>
{banner}
<div class="grid">
<section><h2>Publishing</h2>{publishing}</section>
<section><h2>Feeds</h2>{feeds}</section>
<section><h2>Logging</h2>{logging_}</section>
</div>
<section><h2>Stops</h2>{stops}
<p class="muted">Next: route and minutes as published (~ estimated from another stop, s schedule only).
Drift: learned route/direction offsets at the stop (later than GP Metro predicts is +).</p></section>
<section><h2>Recent events</h2>{events}</section>
<footer class="muted"><a href="/health">/health</a> (JSON) · stop links show every computed field</footer>
</body></html>"""


STYLE = """<style>
:root { --bg:#f6f7f9; --card:#fff; --text:#16181d; --muted:#667085; --line:#e3e6eb;
  --good:#127a3a; --bad:#b42318; --okbg:#e7f6ec; --warnbg:#fdeceb; --link:#1d4ed8; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#111317; --card:#1a1d23; --text:#e7e9ee; --muted:#9097a3; --line:#2b3039;
  --good:#5fd38a; --bad:#ff8a80; --okbg:#15301f; --warnbg:#3a1a19; --link:#8ab4ff; } }
:root[data-theme="dark"] { --bg:#111317; --card:#1a1d23; --text:#e7e9ee; --muted:#9097a3; --line:#2b3039;
  --good:#5fd38a; --bad:#ff8a80; --okbg:#15301f; --warnbg:#3a1a19; --link:#8ab4ff; }
* { box-sizing: border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--text);
  font:14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; max-width:1200px; margin-inline:auto; }
a { color:var(--link); }
header { display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 12px; margin-bottom:12px; }
h1 { font-size:20px; margin:0; } h2 { font-size:15px; margin:0 0 8px; }
.muted { color:var(--muted); } .good { color:var(--good); font-weight:600; } .bad { color:var(--bad); font-weight:600; }
.banner { border-radius:8px; padding:10px 14px; margin-bottom:12px; }
.banner.ok { background:var(--okbg); } .banner.warn { background:var(--warnbg); }
.banner ul { margin:6px 0 0; padding-left:20px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:12px; }
section { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin-bottom:12px; }
.grid section { margin-bottom:0; } .grid { margin-bottom:12px; }
dl { display:grid; grid-template-columns:max-content 1fr; gap:4px 12px; margin:0; }
dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-weight:600; color:var(--muted); font-size:12px; white-space:nowrap; }
td.num { font-variant-numeric:tabular-nums; } td.name { min-width:160px; }
.dep { white-space:nowrap; margin-right:6px; font-variant-numeric:tabular-nums; }
.events td { font-size:13px; } .events .when { white-space:nowrap; color:var(--muted); }
.events .detail { color:var(--muted); overflow-wrap:anywhere; }
footer { margin-top:8px; }
</style>"""
