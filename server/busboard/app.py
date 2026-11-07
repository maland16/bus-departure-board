"""HTTP server: departures JSON, our BMP, and a page comparing us with UCOP.

Run with:  uv run uvicorn busboard.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import html
import logging
import time

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .archive import EventLog, InputArchive
from .compare import CompareStore, compare_stop
from .config import load_config
from .departures import board_departures, compute_departures
from .payload import build_payload
from .realtime import RealtimeFeed
from .render import panel_bmp_to_portrait_png, render_board, to_panel_bmp
from .static_gtfs import StaticFeed, StaticIndex
from .ucop import UcopClient, UcopSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("busboard")
logging.getLogger("httpx").setLevel(logging.WARNING)  # otherwise every 30 s poll is logged

cfg = load_config()
static = StaticFeed(cfg.static_url, cfg.data_dir, cfg.stops)
realtime = RealtimeFeed(cfg.trip_updates_url, cfg.vehicle_positions_url)
store = CompareStore(cfg.data_dir / "compare.db")
events = EventLog(cfg.data_dir)
archive = InputArchive(cfg.data_dir)
ucop_latest: dict[str, UcopSnapshot] = {}
ucop_bmp_cache: dict[str, tuple[float, bytes]] = {}
_ucop_client: UcopClient | None = None
_static_name: str | None = None  # archived file name of the static version behind the current index
_last_rt_error: str | None = None
_match_dip_logged = False
UCOP_BMP_MAX_AGE_S = 60


def _index() -> StaticIndex:
    if static.index is None:
        raise HTTPException(503, "static schedule not loaded yet")
    return static.index


def _check_stop(code: str) -> str:
    if code not in cfg.stops:
        raise HTTPException(404, f"stop {code} is not configured")
    if _index().stop_id_for(code) is None:
        raise HTTPException(404, f"stop {code} is not in GP Metro's current GTFS feed")
    return code


def _active_stops() -> list[str]:
    idx = static.index
    return [c for c in cfg.stops if idx and idx.stop_id_for(c)]


def _ucop() -> UcopClient:
    global _ucop_client
    if _ucop_client is None:
        _ucop_client = UcopClient(cfg.ucop_base_url, cfg.ucop_display_name, _index().tz)
    return _ucop_client


def _departures(code: str, now: int, past_grace_s: int | None = None):
    return compute_departures(
        _index(), realtime.snapshot, code, now,
        window_ahead_s=cfg.window_ahead_s,
        past_grace_s=cfg.past_grace_s if past_grace_s is None else past_grace_s,
        stale_vehicle_s=cfg.stale_vehicle_s,
    )


def _stop_name(code: str) -> str:
    idx = _index()
    return idx.stop_names.get(idx.stop_id_for(code) or "", code)


def trip_match_rate() -> tuple[int, int]:
    """(realtime trips found in the static feed, realtime trips). A drop means the feeds disagree,
    e.g. trip_ids changed in a new nightly build we haven't downloaded yet."""
    if static.index is None:
        return 0, 0
    trips = realtime.snapshot.trips
    return sum(1 for t in trips if t in static.index.trips), len(trips)


# ---- background work -------------------------------------------------------

async def _every(interval_s: float, fn, name: str):
    while True:
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("%s failed", name)
        await asyncio.sleep(interval_s)


def _on_static_index(previous: StaticIndex | None, reason: str):
    """Archive the static version behind the current index and log what changed."""
    global _static_name
    idx = static.index
    if idx is None:
        return
    try:
        _static_name = archive.save_static(static.zip_path, idx.version, static.fetched_at, cfg.static_versions_kept)
    except OSError:
        log.exception("could not archive static feed")
    fields = {
        "reason": reason, "version": idx.version, "archived_as": _static_name, "trips": len(idx.trips),
        "service_dates": [idx.first_date, idx.last_date], "missing_stops": idx.missing_stop_codes,
        "visits": {c: len(idx.visits.get(idx.stop_id_for(c) or "", ())) for c in cfg.stops},
    }
    if previous is not None and previous is not idx:
        old, new = set(previous.trips), set(idx.trips)
        # Whether trip_ids survive the nightly rebuild (an open question about this feed)
        fields.update(previous_version=previous.version, trips_kept=len(old & new),
                      trips_added=len(new - old), trips_removed=len(old - new))
    events.write("static_index", **fields)


def _static_tick():
    previous = static.index
    if static.refresh():
        _on_static_index(previous, "refresh")
    elif static.last_error:
        events.write("static_error", error=static.last_error)


def _realtime_tick():
    global _last_rt_error, _match_dip_logged
    realtime.refresh()
    if realtime.last_error != _last_rt_error:  # log changes, not every failed poll
        events.write("realtime_error" if realtime.last_error else "realtime_recovered", error=realtime.last_error)
        _last_rt_error = realtime.last_error
    matched, total = trip_match_rate()
    dipped = total >= 20 and matched / total < 0.9
    if dipped and not _match_dip_logged:
        unmatched = [t for t in realtime.snapshot.trips if static.index and t not in static.index.trips]
        events.write("trip_match_dip", matched=matched, total=total, static_version=static.index.version,
                     unmatched_sample=unmatched[:20])
    _match_dip_logged = dipped
    if dipped and time.time() - static.last_check > 600:
        log.warning("only %d/%d realtime trips are in the static feed; re-downloading it", matched, total)
        previous = static.index
        if static.refresh(force=True):
            _on_static_index(previous, "trip_match_dip")


def _compare_tick():
    if static.index is None:
        return
    idx, rt_snap = static.index, realtime.snapshot  # same inputs for every stop in this round
    now = time.time()
    pages: dict[str, UcopSnapshot] = {}
    for code in _active_stops():
        pages[code] = _ucop().fetch_page(code)
    files = {"TripUpdate.pb": rt_snap.trip_updates_raw, "VehiclePosition.pb": rt_snap.vehicle_positions_raw}
    files.update({f"ucop-{code}.html": snap.html for code, snap in pages.items()})
    meta = {
        "ts": int(now), "static": _static_name, "static_version": idx.version, "stops": list(pages),
        "trip_updates_fetched_at": rt_snap.trip_updates_fetched_at,
        "trip_updates_header_ts": rt_snap.trip_updates_header_ts,
        "vehicle_positions_fetched_at": rt_snap.vehicle_positions_fetched_at,
        "vehicle_positions_header_ts": rt_snap.vehicle_positions_header_ts,
        "ucop": {code: {"fetched_at": snap.fetched_at, "error": snap.error} for code, snap in pages.items()},
    }
    archive_rel = None
    try:
        path = archive.save_snapshot(now, meta, files)
        archive_rel = str(path.relative_to(cfg.data_dir))
    except OSError:
        log.exception("could not archive comparison inputs")

    for code, snap in pages.items():
        ucop_latest[code] = snap
        t = int(snap.fetched_at)
        ours, rows = compare_stop(idx, rt_snap, snap.arrivals, code, t, cfg)
        if snap.error and not snap.arrivals:
            rows = []
            events.write("ucop_error", stop=code, error=snap.error)
        store.record(code, t, rows, len(snap.arrivals), len(ours), snap.error, archive_rel)
    store.prune(cfg.retention_days)
    archive.prune(cfg.archive_days)
    events.prune(max(cfg.retention_days, cfg.archive_days))


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    events.write("startup", stops=cfg.stops)
    if await asyncio.to_thread(static.load_cached):
        _on_static_index(None, "startup_cache")
    await asyncio.to_thread(_static_tick)
    await asyncio.to_thread(realtime.refresh)
    tasks = [
        asyncio.create_task(_every(cfg.static_check_interval_s, _static_tick, "static refresh")),
        asyncio.create_task(_every(cfg.realtime_poll_interval_s, _realtime_tick, "realtime refresh")),
        asyncio.create_task(_every(cfg.compare_interval_s, _compare_tick, "ucop compare")),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="busboard", lifespan=lifespan)


# ---- endpoints -------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/compare")


@app.get("/stops/{code}/departures")
def departures_json(code: str, debug: bool = False):
    code = _check_stop(code)
    now = int(time.time())
    deps = _departures(code, now)
    if debug:
        return {"now": now, "departures": [d.to_dict() for d in deps]}
    return build_payload(code, _stop_name(code), board_departures(deps, cfg.board_rows), now)


def _our_bmp(code: str) -> bytes:
    now = int(time.time())
    deps = board_departures(_departures(code, now), cfg.board_rows)
    img = render_board(code, _stop_name(code), deps, now, _index().tz, rows=cfg.board_rows)
    return to_panel_bmp(img)


@app.get("/stops/{code}/display.bmp")
def display_bmp(code: str):
    return Response(_our_bmp(_check_stop(code)), media_type="image/bmp", headers={"Cache-Control": "no-cache"})


@app.get("/stops/{code}/preview.png")
def preview_png(code: str, source: str = "ours"):
    """Upright PNG of our image or UCOP's latest, for the comparison page."""
    code = _check_stop(code)
    if source == "ucop":
        # Fetched only when someone looks, to keep requests to UCOP down
        cached = ucop_bmp_cache.get(code)
        if not cached or time.time() - cached[0] > UCOP_BMP_MAX_AGE_S:
            try:
                ucop_bmp_cache[code] = cached = (time.time(), _ucop().fetch_bmp(code))
            except Exception as e:
                if not cached:
                    raise HTTPException(502, f"could not fetch UCOP image: {e}")
        bmp = cached[1]
    else:
        bmp = _our_bmp(code)
    return Response(panel_bmp_to_portrait_png(bmp), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    now = time.time()
    snap = realtime.snapshot
    idx = static.index
    matched, total = trip_match_rate()
    ok = idx is not None and snap.trip_updates_fetched_at and now - snap.trip_updates_fetched_at < 300
    body = {
        "ok": bool(ok),
        "static": {
            "loaded": idx is not None,
            "version": idx.version if idx else None,
            "service_dates": [idx.first_date, idx.last_date] if idx else None,
            "trips": len(idx.trips) if idx else 0,
            "missing_stops": idx.missing_stop_codes if idx else None,
            "last_check_age_s": round(now - static.last_check) if static.last_check else None,
            "error": static.last_error,
        },
        "realtime": {
            "trip_updates_age_s": round(now - snap.trip_updates_fetched_at) if snap.trip_updates_fetched_at else None,
            "trip_updates_feed_lag_s": round(now - snap.trip_updates_header_ts) if snap.trip_updates_header_ts else None,
            "vehicle_positions_age_s": round(now - snap.vehicle_positions_fetched_at) if snap.vehicle_positions_fetched_at else None,
            "trips": total,
            "trips_in_static": matched,
            "vehicles": len(snap.vehicles_by_trip),
            "error": realtime.last_error,
        },
        "ucop": {
            code: {"age_s": round(now - s.fetched_at), "arrivals": len(s.arrivals), "error": s.error}
            for code, s in ucop_latest.items()
        },
    }
    return JSONResponse(body, status_code=200 if ok else 503)


# ---- comparison page -------------------------------------------------------

SRC_LABEL = {2: "stop", 1: "origin", 0: "sched", None: ""}


def _fmt_time(epoch, tz):
    if epoch is None:
        return ""
    return dt.datetime.fromtimestamp(epoch, tz).strftime("%-I:%M %p").lower()


def _pct(n, d):
    return f"{100 * (n or 0) / d:.0f}%" if d else "–"


def _summary_table(s: dict) -> str:
    def col(c):
        listed = (c["match"] or 0) + (c["time_diff"] or 0)
        everything = listed + (c["ours_only"] or 0) + (c["ucop_only"] or 0)
        return [
            f"{c['snapshots'] or 0}",
            f"{c['trips'] or 0}",
            f"{_pct(listed, everything)} ({listed}/{everything})",
            f"{_pct(c['match'], listed)} ({c['match'] or 0}/{listed})",
            f"{c['time_diff_stale_vehicle'] or 0}",
            f"{c['time_diff_stale_block'] or 0}",
            f"{c['time_diff_src2'] or 0} / {c['time_diff_src1'] or 0} / {c['time_diff_src0'] or 0}",
            f"{c['ours_only'] or 0}",
            f"{c['ucop_only'] or 0}",
            f"{c['src2'] or 0} / {c['src1'] or 0} / {c['src0'] or 0}",
            f"{c['headsign_differs'] or 0}",
        ]
    labels = [
        "Snapshots", "Distinct trips", "Listed by both", "Times within tolerance (of listed by both)",
        "Time diffs: stale vehicle (expected)", "Time diffs: stale block",
        "Other time diffs by our source: stop / origin / sched", "Only in ours", "Only in UCOP",
        "Our source when both list it: stop / origin / sched", "Headsign differs",
    ]
    a, n = col(s["all"]), col(s["near_term"])
    body = "".join(f"<tr><th>{html.escape(l)}</th><td>{x}</td><td>{y}</td></tr>" for l, x, y in zip(labels, a, n))
    return f"<table><tr><th></th><th>All rows</th><th>Due within 15 min</th></tr>{body}</table>"


def _rows_table(rows: list[dict], tz, with_ts=False) -> str:
    head = ("<th>Seen</th>" if with_ts else "") + (
        "<th>Route</th><th>Trip</th><th>Category</th><th>Sched</th><th>Ours</th><th>Src</th>"
        "<th>UCOP</th><th>UCOP status</th><th>Diff</th><th>Veh age</th><th>Our headsign</th><th>UCOP headsign</th><th>Flags</th>"
    )
    out = []
    for r in rows:
        flags = " ".join(f for f, on in (("stale-vehicle", r["stale_vehicle"]), ("stale-block", r["stale_block"])) if on)
        diff = "" if r["diff_s"] is None else f"{r['diff_s'] / 60:+.1f} min"
        cells = ([_fmt_time(r["ts"], tz)] if with_ts else []) + [
            r["route"], r["trip_id"], r["category"], _fmt_time(r["our_sched"] or r["ucop_sched"], tz),
            _fmt_time(r["our_est"], tz), SRC_LABEL.get(r["our_src"], ""), _fmt_time(r["ucop_est"], tz),
            r["ucop_status"] or "", diff,
            "" if r.get("vehicle_age_s") is None else f"{r['vehicle_age_s']} s",
            r["our_headsign"] or "", r["ucop_headsign"] or "", flags,
        ]
        cls = r["category"] + (" headsign" if r["headsign_differs"] else "")
        out.append(f'<tr class="{cls}">' + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
    return f"<table class='rows'><tr>{head}</tr>{''.join(out)}</table>"


PAGE_STYLE = """<style>
 body { font: 14px system-ui, sans-serif; margin: 16px; background: #fafafa; color: #111; }
 .imgs { display: flex; gap: 16px; flex-wrap: wrap; }
 .imgs figure { margin: 0; } .imgs img { width: 360px; max-width: 100%; border: 1px solid #999; background: #fff; }
 table { border-collapse: collapse; margin: 8px 0 24px; background: #fff; }
 th, td { border: 1px solid #ddd; padding: 3px 6px; text-align: left; vertical-align: top; }
 .rows { font-size: 13px; }
 tr.time_diff td { background: #fff3cd; } tr.ours_only td, tr.ucop_only td { background: #f8d7da; }
 tr.ucop_departed td, tr.ours_terminating td { color: #888; } tr.headsign td:nth-last-child(2), tr.headsign td:nth-last-child(3) { font-weight: 600; }
 .wrap { overflow-x: auto; }
</style>"""


@app.get("/compare", response_class=HTMLResponse)
def compare_overview():
    """One row per configured stop, last 24 h."""
    idx = _index()
    now = int(time.time())
    rows = []
    for code in cfg.stops:
        if idx.stop_id_for(code) is None:
            rows.append(f"<tr><td>{html.escape(code)}</td><td colspan='8'>Not in GP Metro's current GTFS feed</td></tr>")
            continue
        c = store.summary(code, now - 86400)["all"]
        listed = (c["match"] or 0) + (c["time_diff"] or 0)
        everything = listed + (c["ours_only"] or 0) + (c["ucop_only"] or 0)
        other_diffs = (c["time_diff"] or 0) - (c["time_diff_stale_vehicle"] or 0) - (c["time_diff_stale_block"] or 0)
        cells = [
            f"<a href='/compare/{code}'>{code}</a>", html.escape(_stop_name(code)), str(c["snapshots"] or 0),
            _pct(listed, everything), _pct(c["match"], listed), str(c["time_diff_stale_vehicle"] or 0),
            str(c["time_diff_stale_block"] or 0), str(other_diffs),
            f"{c['ours_only'] or 0} / {c['ucop_only'] or 0}", str(c["headsign_differs"] or 0),
        ]
        rows.append("<tr>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>")
    head = ("<tr><th>Stop</th><th>Name</th><th>Snapshots</th><th>Listed by both</th><th>Times match</th>"
            "<th>Diffs: stale vehicle</th><th>Diffs: stale block</th><th>Other diffs</th>"
            "<th>Only ours / only UCOP</th><th>Headsign differs</th></tr>")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120"><title>Board comparison</title>{PAGE_STYLE}</head><body>
<h1>Our departures vs UCOP, last 24 hours</h1>
<p>Counts are comparison rows: each trip is compared every {cfg.compare_interval_s // 60} min while both lists show it.
 Times match if within {cfg.match_tolerance_s} s. "Stale vehicle" differences are deliberate (we ignore predictions
 from buses that stopped reporting).</p>
<div class="wrap"><table>{head}{''.join(rows)}</table></div>
</body></html>"""


@app.get("/compare/{code}", response_class=HTMLResponse)
def compare_page(code: str):
    code = _check_stop(code)
    tz = _index().tz
    now = int(time.time())
    snap, rows = store.latest(code)
    day = store.summary(code, now - 86400)
    mismatches = store.recent_mismatches(code, now - 86400)
    snap_line = (
        f"Latest comparison at {_fmt_time(snap['ts'], tz)}: UCOP listed {snap['ucop_count']}, "
        f"we computed {snap['ours_count']} (incl. past).{' UCOP error: ' + html.escape(snap['ucop_error']) if snap['ucop_error'] else ''}"
        if snap else "No comparison yet; the first one runs a minute or two after startup."
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Board comparison</title>{PAGE_STYLE}</head><body>
<p><a href="/compare">&larr; All stops</a></p>
<h1>Stop {code}: {html.escape(_stop_name(code))}</h1>
<p>Our board vs UCOP. Times match if within {cfg.match_tolerance_s} s (UCOP rounds to the minute).
 Our source: <b>stop</b> = predicted for this stop, <b>origin</b> = estimated from the trip's first stop,
 <b>sched</b> = schedule only. <b>stale-vehicle</b> = we ignored the prediction because the bus hasn't
 reported its position for over {cfg.stale_vehicle_s // 60} min.</p>
<div class="imgs">
 <figure><img src="/stops/{code}/preview.png?source=ours&t={now}" alt="Our board"><figcaption>Ours (now)</figcaption></figure>
 <figure><img src="/stops/{code}/preview.png?source=ucop&t={now}" alt="UCOP board"><figcaption>UCOP (fetched when this page loads)</figcaption></figure>
</div>
<h2>Last 24 hours</h2>
{_summary_table(day)}
<h2>Latest comparison</h2>
<p>{snap_line}</p>
<div class="wrap">{_rows_table(rows, tz)}</div>
<h2>Recent disagreements (last 24 h, latest per trip)</h2>
<div class="wrap">{_rows_table(mismatches, tz, with_ts=True)}</div>
</body></html>"""
