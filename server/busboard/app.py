"""The server: keeps each stop's departures JSON published for the boards, and logs what it needs to
improve the estimates (archived feeds, the predictions it served, observed departures).

Run with:  uv run uvicorn busboard.app:app --host 0.0.0.0 --port 8080

Work, all off the request path:
  every realtime_poll_interval_s  fetch the realtime feeds, note departures seen in vehicle positions,
                                  compute every stop's departures once, write and push the payloads
                                  (and every log_interval_s, also log the predictions and archive the feeds)
  every static_check_interval_s   check for a new static feed
  every verify_interval_s         fetch the published index.json back from the web host
  hourly                          prune old logs, measure disk use
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import shutil
import sys
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from . import dashboard
from .archive import EventLog, InputArchive
from .config import load_config
from .departures import Departure, board_departures, compute_departures
from .drift import DriftModel
from .observe import DepartureObserver
from .payload import build_payload
from .publish import Publisher
from .realtime import RealtimeFeed, RealtimeSnapshot
from .static_gtfs import StaticFeed, StaticIndex
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("busboard")
logging.getLogger("httpx").setLevel(logging.WARNING)  # otherwise every 30 s poll is logged

HOUSEKEEPING_INTERVAL_S = 3600
SERVED_STALE_S = 120  # the web host serving data older than this is a problem
LOW_DISK_BYTES = 1 << 30

cfg = load_config()
static = StaticFeed(cfg.static_url, cfg.data_dir, cfg.stops)
realtime = RealtimeFeed(cfg.trip_updates_url, cfg.vehicle_positions_url, cfg.realtime_hold_missing_s)
publisher = Publisher(cfg.publish_target, cfg.publish_public_url, cfg.publish_ssh_key, cfg.data_dir)
PUBLISHING = cfg.publish_enabled and bool(cfg.publish_target)
drift = DriftModel(window_s=cfg.drift_window_s) if cfg.drift_correction else None
store = Store(cfg.data_dir / "busboard.db")
events = EventLog(cfg.data_dir)
archive = InputArchive(cfg.data_dir)
observer: DepartureObserver | None = None
started_at = time.time()

_static_name: str | None = None  # archived file name of the static version behind the current index
# Trips in the previous static version that the current one dropped (e.g. a holiday's service the
# morning after). Their buses can linger in the realtime feed as ghosts; that's not a trip_id change.
_retired_trips: frozenset[str] = frozenset()
_last_rt_error: str | None = None
_match_dip_logged = False
_last_publish_error: str | None = None
_served_stale_logged = False
_last_logged: int | None = None  # when predictions were last logged and the feeds archived
_payloads: dict[str, dict] = {}  # stop code -> the payload last computed (and published)
cycle_status: dict = {"at": None, "took_ms": None, "error": None, "logged_rows": None}


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


def realtime_is_live(now: float | None = None) -> bool:
    fetched = realtime.snapshot.trip_updates_fetched_at
    return bool(fetched) and (now or time.time()) - fetched <= cfg.realtime_max_age_s


def _display_realtime() -> RealtimeSnapshot:
    """The realtime data to build departures from: none at all once the TripUpdate feed has been
    failing for realtime_max_age_s, so old predictions aren't served as if they were current."""
    return realtime.snapshot if realtime_is_live() else RealtimeSnapshot()


def _stop_name(idx: StaticIndex, code: str) -> str:
    stop_id = idx.stop_id_for(code)
    return idx.stop_display_name(stop_id) if stop_id else code


def _compute(idx: StaticIndex, rt: RealtimeSnapshot, code: str, now: int, learn: bool = False) -> list[Departure]:
    """A stop's departures, drift-corrected. learn: also hand the raw estimates to the drift model."""
    deps = compute_departures(
        idx, rt, code, now,
        window_ahead_s=cfg.window_ahead_s, past_grace_s=cfg.past_grace_s,
        stale_vehicle_s=cfg.stale_vehicle_s, block_layover_s=cfg.block_layover_s,
    )
    if drift is None:
        return deps
    if learn:
        drift.record(idx, deps, now)
    return drift.correct(idx, [dataclasses.replace(d) for d in deps], now)


def _payload(code: str, now: int, deps: list[Departure] | None = None) -> dict:
    idx = _index()
    if deps is None:
        deps = _compute(idx, _display_realtime(), code, now)
    return build_payload(code, _stop_name(idx, code), board_departures(deps, cfg.payload_rows), now, idx.tz,
                         live=realtime_is_live(now))


def trip_match_rate() -> tuple[int, int]:
    """(realtime trips found in the static feed, realtime trips). A drop means the feeds disagree,
    e.g. trip_ids changed in a new nightly build we haven't downloaded yet."""
    if static.index is None:
        return 0, 0
    trips = [t for t in realtime.snapshot.trips if t not in _retired_trips]
    return sum(1 for t in trips if t in static.index.trips), len(trips)


# ---- background work -------------------------------------------------------

async def _every(interval_s: float, fn, name: str, first_delay_s: float = 0):
    """Run fn in a thread on a fixed cadence (a slow run doesn't push the schedule back, an overrun
    starts the next one straight away)."""
    await asyncio.sleep(first_delay_s)
    next_at = time.monotonic()
    while True:
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("%s failed", name)
        next_at += interval_s
        delay = next_at - time.monotonic()
        if delay < 0:
            next_at, delay = time.monotonic(), 0
        await asyncio.sleep(delay)


def _on_static_index(previous: StaticIndex | None, reason: str):
    """Archive the static version behind the current index and log what changed."""
    global _static_name, _retired_trips
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
        _retired_trips = frozenset(old - new)
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


def _refresh_realtime():
    global _last_rt_error, _match_dip_logged
    realtime.refresh()
    if realtime.last_error != _last_rt_error:  # log changes, not every failed poll
        events.write("realtime_error" if realtime.last_error else "realtime_recovered", error=realtime.last_error)
        _last_rt_error = realtime.last_error
    matched, total = trip_match_rate()
    dipped = total >= 20 and matched / total < 0.9
    if dipped and not _match_dip_logged:
        unmatched = [t for t in realtime.snapshot.trips
                     if static.index and t not in static.index.trips and t not in _retired_trips]
        events.write("trip_match_dip", matched=matched, total=total, static_version=static.index.version,
                     unmatched_sample=unmatched[:20])
    _match_dip_logged = dipped
    if dipped and time.time() - static.last_check > 600:
        log.warning("only %d/%d realtime trips are in the static feed; re-downloading it", matched, total)
        previous = static.index
        if static.refresh(force=True):
            _on_static_index(previous, "trip_match_dip")


def _observe_departures(idx: StaticIndex, now: int):
    """Record when buses actually left the watched stops (ground truth for accuracy and drift)."""
    global observer
    stop_codes = {idx.stop_id_for(c): c for c in _active_stops()}
    if observer is None or observer.stop_ids != set(stop_codes):
        observer = DepartureObserver(set(stop_codes))
    seen = observer.update(idx, realtime.snapshot.vehicles_by_trip)
    if drift:
        for o in seen:
            drift.resolve(o)
        drift.prune(now)
    if seen:
        routes = {o.trip_id: idx.routes[idx.trips[o.trip_id].route_id].short_name
                  for o in seen if o.trip_id in idx.trips and idx.trips[o.trip_id].route_id in idx.routes}
        store.record_departures(seen, stop_codes, routes)
    observer.forget_older_than(now - 2 * 3600)


def _archive_feeds(idx: StaticIndex, now: int):
    snap = realtime.snapshot
    meta = {
        "ts": now, "static": _static_name, "static_version": idx.version, "stops": _active_stops(),
        "trip_updates_fetched_at": snap.trip_updates_fetched_at,
        "trip_updates_header_ts": snap.trip_updates_header_ts,
        "vehicle_positions_fetched_at": snap.vehicle_positions_fetched_at,
        "vehicle_positions_header_ts": snap.vehicle_positions_header_ts,
    }
    try:
        archive.save_snapshot(now, meta, {"TripUpdate.pb": snap.trip_updates_raw,
                                          "VehiclePosition.pb": snap.vehicle_positions_raw})
    except OSError:
        log.exception("could not archive the realtime feeds")


def _cycle():
    """Fetch realtime data, then compute, publish and (every log_interval_s) log every stop's departures."""
    global _payloads, _last_logged
    start = time.monotonic()
    error = None
    try:
        _refresh_realtime()
        idx = static.index
        if idx is None:
            error = "static schedule not loaded"
            return
        now = int(time.time())
        _observe_departures(idx, now)
        log_due = _last_logged is None or now - _last_logged >= cfg.log_interval_s - cfg.realtime_poll_interval_s / 2
        rt = _display_realtime()
        live = realtime_is_live(now)
        payloads, logged = {}, 0
        for code in _active_stops():
            try:
                deps = _compute(idx, rt, code, now, learn=log_due)
                shown = board_departures(deps, cfg.payload_rows)
                payloads[code] = build_payload(code, _stop_name(idx, code), shown, now, idx.tz, live=live)
                if log_due:
                    logged += store.record_predictions(
                        code, now,
                        [d for d in deps if not d.ends_here and not d.canceled and now <= d.est <= now + cfg.log_ahead_s],
                        {(d.trip_id, d.stop_sequence) for d in shown})
            except Exception:
                # One stop's odd data mustn't cost every other stop its update
                log.exception("departures failed for stop %s", code)
        _payloads = payloads
        if log_due:
            _archive_feeds(idx, now)
            _last_logged = now
            cycle_status["logged_rows"] = logged
        if PUBLISHING:
            _publish(payloads, now)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        cycle_status.update(at=time.time(), took_ms=int((time.monotonic() - start) * 1000), error=error)


def _publish(payloads: dict[str, dict], now: int):
    global _last_publish_error
    if not payloads:
        return  # nothing to say yet; don't publish an empty index
    publisher.write(payloads, now)
    publisher.push()
    if publisher.last_error != _last_publish_error:  # log changes, not every failed push
        events.write("publish_error" if publisher.last_error else "publish_recovered",
                     error=publisher.last_error, target=cfg.publish_target)
        _last_publish_error = publisher.last_error


def _verify_tick():
    """Fetch what the boards would get back from the web host."""
    global _served_stale_logged
    if not publisher.last_ok:
        return
    lag = publisher.verify()
    stale = lag is None or lag > SERVED_STALE_S
    if stale and not _served_stale_logged:
        events.write("publish_served_stale", served_lag_s=lag, error=publisher.verify_error,
                     url=f"{cfg.publish_public_url}/index.json")
    _served_stale_logged = stale


def _housekeeping():
    store.prune(cfg.retention_days)
    archive.prune(cfg.archive_days)
    events.prune(max(cfg.retention_days, cfg.archive_days))
    archive.measure()
    dashboard.invalidate()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    events.write("startup", stops=cfg.stops, publishing=PUBLISHING)
    migrated = await asyncio.to_thread(store.migrate_legacy)
    if migrated:
        events.write("store_migrated", **migrated)
    if await asyncio.to_thread(static.load_cached):
        _on_static_index(None, "startup_cache")
    await asyncio.to_thread(_static_tick)
    if drift and static.index is not None:
        # Pick up where the last run left off instead of re-learning for hours after each restart
        rows = await asyncio.to_thread(store.drift_training, int(time.time()) - cfg.drift_window_s)
        log.info("drift model warm start: %d samples", drift.warm_start(static.index, rows))
    tasks = [
        asyncio.create_task(_every(cfg.realtime_poll_interval_s, _cycle, "realtime cycle")),
        asyncio.create_task(_every(cfg.static_check_interval_s, _static_tick, "static refresh",
                                   first_delay_s=cfg.static_check_interval_s)),
        asyncio.create_task(_every(HOUSEKEEPING_INTERVAL_S, _housekeeping, "housekeeping", first_delay_s=60)),
        *([asyncio.create_task(_every(cfg.publish_verify_interval_s, _verify_tick, "publish verify",
                                      first_delay_s=30))] if PUBLISHING else []),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="busboard", lifespan=lifespan)


# ---- status ----------------------------------------------------------------

def problems(now: float | None = None) -> list[str]:
    """What's wrong right now, worst first (empty: all good)."""
    now = now or time.time()
    out = []
    idx = static.index
    if idx is None:
        out.append("The static schedule isn't loaded, so nothing is being published.")
    fetched = realtime.snapshot.trip_updates_fetched_at
    if not realtime_is_live(now):
        out.append("GP Metro's TripUpdate feed hasn't been fetched for "
                   f"{'ever' if not fetched else f'{int(now - fetched) // 60} min'}: the boards show the schedule only.")
    elif realtime.last_error:
        out.append(f"The last realtime fetch failed: {realtime.last_error}")
    if PUBLISHING:
        if publisher.last_error:
            out.append(f"The last push to the web host failed: {publisher.last_error}")
        elif publisher.last_ok is None and time.time() - started_at > 120:
            out.append("Nothing has been pushed to the web host since startup.")
        if publisher.last_verify and (publisher.served_lag_s is None or publisher.served_lag_s > SERVED_STALE_S):
            out.append("The web host was serving old data at the last check "
                       f"({publisher.verify_error or f'{publisher.served_lag_s} s old'}).")
    else:
        out.append("Publishing is off (see [publish] in config.toml): the boards aren't getting updates from here.")
    if cycle_status["at"] and now - cycle_status["at"] > 3 * cfg.realtime_poll_interval_s + 60:
        out.append(f"The update cycle hasn't run for {int(now - cycle_status['at'])} s.")
    matched, total = trip_match_rate()
    if total >= 20 and matched / total < 0.9:
        out.append(f"Only {matched}/{total} realtime trips are in the static feed.")
    if idx is not None and idx.missing_stop_codes:
        out.append(f"Stops not in the current static feed: {', '.join(idx.missing_stop_codes)}.")
    if static.last_error:
        out.append(f"The last static feed check failed: {static.last_error}")
    free = shutil.disk_usage(cfg.data_dir).free if cfg.data_dir.exists() else None
    if free is not None and free < LOW_DISK_BYTES:
        out.append(f"Only {free / 1e9:.1f} GB of disk left.")
    return out


@app.get("/", response_class=HTMLResponse)
def status_page():
    return dashboard.render(sys.modules[__name__])


@app.get("/stops/{code}/departures")
def departures_json(code: str, debug: bool = False):
    """The payload as last published (computed now if there isn't one yet); debug: every field, now."""
    code = _check_stop(code)
    now = int(time.time())
    if debug:
        deps = _compute(_index(), _display_realtime(), code, now)
        return {"now": now, "live": realtime_is_live(now), "departures": [d.to_dict() for d in deps]}
    return _payloads.get(code) or _payload(code, now)


@app.get("/health")
def health():
    now = time.time()
    snap = realtime.snapshot
    idx = static.index
    matched, total = trip_match_rate()
    ok = idx is not None and snap.trip_updates_fetched_at and now - snap.trip_updates_fetched_at < 300
    body = {
        "ok": bool(ok),
        "problems": problems(now),
        "uptime_s": round(now - started_at),
        "cycle": {
            "age_s": round(now - cycle_status["at"]) if cycle_status["at"] else None,
            "took_ms": cycle_status["took_ms"], "error": cycle_status["error"],
        },
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
        "publish": publisher.status() if PUBLISHING else "disabled",
        "logging": {
            "last_logged_age_s": round(now - _last_logged) if _last_logged else None,
            "last_logged_rows": cycle_status["logged_rows"],
            "archive_bytes": archive.size_bytes,
            "db_bytes": store.size_bytes(),
            "disk_free_bytes": shutil.disk_usage(cfg.data_dir).free if cfg.data_dir.exists() else None,
        },
        "drift": _drift_status(now) if drift and idx else "disabled",
    }
    return JSONResponse(body, status_code=200 if ok else 503)


def _drift_status(now: float) -> dict:
    st = drift.status(static.index, int(now))
    biggest = sorted(st.items(), key=lambda kv: -abs(kv[1]["offset_s"]))[:8]
    return {"keys": len(st), "largest_offset_s": {k: v["offset_s"] for k, v in biggest}}
