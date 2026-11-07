"""GP Metro's static GTFS feed: downloading it, and indexing the scheduled visits to our stops.

Notes on this particular feed (see also the project notes on GP Metro's feeds):
- calendar.txt is empty; service days come from calendar_dates.txt, a rolling ~30-day window.
- The zip is rebuilt nightly, and trip_ids look generated, so the index is rebuilt whenever the zip changes.
- ~26% of trips have a blank trip_headsign and there are no stop_headsigns.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "busboard/0.1 (bus departure board server)"
NOON_MINUS_12H = 12 * 3600

# Words that stay upper case when title-casing ALL CAPS headsigns
UPPER_WORDS = {"JC", "SMCC", "BRZ", "HSK", "US", "ME", "UNE", "USM", "VA", "II", "III"}


@dataclass(frozen=True)
class Route:
    route_id: str
    short_name: str
    long_name: str
    color: str


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    headsign: str  # already cleaned up, falls back to the last stop's name, then the route name
    block_id: str
    origin_stop_sequence: int
    origin_departure_secs: int
    last_stop_sequence: int


@dataclass(frozen=True)
class StopVisit:
    trip_id: str
    stop_sequence: int
    departure_secs: int  # seconds from the service day's base (noon - 12h); can exceed 24h
    timepoint: bool = True  # GTFS: blank or 1 = exact time (buses usually wait rather than leave early)


@dataclass(frozen=True)
class ScheduledVisit:
    service_date: str  # YYYYMMDD
    visit: StopVisit
    trip: Trip
    sched: int  # epoch seconds

    @property
    def ends_here(self) -> bool:
        return self.visit.stop_sequence == self.trip.last_stop_sequence


def tidy_headsign(text: str) -> str:
    """Title-case ALL CAPS words ("FALMOUTH SHAW'S" -> "Falmouth Shaw's"), keeping known acronyms.
    Works word by word, since some headsigns are mixed ("THOMAS ST + Millcreek Hub")."""

    def word(w: str) -> str:
        if len(w) < 2 or not w.isupper() or w in UPPER_WORDS or not w[:1].isalpha():
            return w
        return "-".join(p[:1].upper() + p[1:].lower() for p in w.split("-"))

    return " ".join(word(w) for w in text.split())


def parse_gtfs_time(value: str) -> int:
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def service_day_base(date: dt.date, tz: ZoneInfo) -> int:
    """GTFS times count from "noon minus 12h" on the service day, which differs from midnight on DST days."""
    noon = dt.datetime(date.year, date.month, date.day, 12, tzinfo=tz)
    return int(noon.timestamp()) - NOON_MINUS_12H


class StaticIndex:
    def __init__(
        self,
        *,
        tz: ZoneInfo,
        stop_ids_by_code: dict[str, str],
        stop_names: dict[str, str],
        routes: dict[str, Route],
        trips: dict[str, Trip],
        visits: dict[str, list[StopVisit]],
        services_by_date: dict[str, set[str]],
        version: str,
        missing_stop_codes: list[str] | None = None,
    ):
        self.tz = tz
        self.stop_ids_by_code = stop_ids_by_code
        self.stop_names = stop_names
        self.routes = routes
        self.trips = trips
        self.visits = visits
        self.services_by_date = services_by_date
        self.version = version
        self.missing_stop_codes = missing_stop_codes or []
        self.loaded_at = time.time()

    @property
    def first_date(self) -> str | None:
        return min(self.services_by_date, default=None)

    @property
    def last_date(self) -> str | None:
        return max(self.services_by_date, default=None)

    def stop_id_for(self, stop_code: str) -> str | None:
        return self.stop_ids_by_code.get(stop_code)

    def scheduled_between(self, stop_id: str, start: int, end: int) -> list[ScheduledVisit]:
        """Scheduled visits to stop_id with a departure time in [start, end], across service days."""
        out = []
        first = dt.datetime.fromtimestamp(start, self.tz).date() - dt.timedelta(days=1)
        last = dt.datetime.fromtimestamp(end, self.tz).date()
        day = first
        while day <= last:
            key = day.strftime("%Y%m%d")
            services = self.services_by_date.get(key)
            if services:
                base = service_day_base(day, self.tz)
                for v in self.visits.get(stop_id, ()):
                    trip = self.trips[v.trip_id]
                    if trip.service_id in services:
                        sched = base + v.departure_secs
                        if start <= sched <= end:
                            out.append(ScheduledVisit(key, v, trip, sched))
            day += dt.timedelta(days=1)
        out.sort(key=lambda s: s.sched)
        return out

    def origin_sched(self, trip: Trip, service_date: str) -> int:
        day = dt.datetime.strptime(service_date, "%Y%m%d").date()
        return service_day_base(day, self.tz) + trip.origin_departure_secs


def _rows(zf: zipfile.ZipFile, name: str):
    try:
        with zf.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
    except KeyError:
        return


def build_index(zip_bytes: bytes, stop_codes: list[str], version: str = "") -> StaticIndex:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

    tz_name = next((r["agency_timezone"] for r in _rows(zf, "agency.txt")), "America/New_York")
    tz = ZoneInfo(tz_name)

    stop_names, stop_ids_by_code = {}, {}
    for r in _rows(zf, "stops.txt"):
        stop_names[r["stop_id"]] = r["stop_name"].strip()
        code = (r.get("stop_code") or "").strip()
        # Prefer the boarding stop (location_type 0/blank) over a parent station with the same code
        if code and (code not in stop_ids_by_code or r.get("location_type", "0") in ("", "0")):
            stop_ids_by_code[code] = r["stop_id"]
    wanted = {stop_ids_by_code[c] for c in stop_codes if c in stop_ids_by_code}
    missing = [c for c in stop_codes if c not in stop_ids_by_code]
    if missing:
        log.warning("stop codes not in static feed: %s", missing)

    routes = {
        r["route_id"]: Route(
            r["route_id"],
            (r.get("route_short_name") or r["route_id"]).strip(),
            (r.get("route_long_name") or "").strip(),
            (r.get("route_color") or "").strip(),
        )
        for r in _rows(zf, "routes.txt")
    }

    # One pass over stop_times: our stops' visits, plus each trip's first and last stop
    visits: dict[str, list[StopVisit]] = {s: [] for s in wanted}
    first: dict[str, tuple[int, int]] = {}  # trip -> (seq, departure_secs)
    last: dict[str, tuple[int, str]] = {}  # trip -> (seq, stop_id)
    for r in _rows(zf, "stop_times.txt"):
        trip_id, stop_id = r["trip_id"], r["stop_id"]
        seq = int(r["stop_sequence"])
        dep = r.get("departure_time") or r.get("arrival_time") or ""
        if stop_id in wanted and dep:
            visits[stop_id].append(StopVisit(trip_id, seq, parse_gtfs_time(dep), (r.get("timepoint") or "1") != "0"))
        if dep and (trip_id not in first or seq < first[trip_id][0]):
            first[trip_id] = (seq, parse_gtfs_time(dep))
        if trip_id not in last or seq > last[trip_id][0]:
            last[trip_id] = (seq, stop_id)

    trips = {}
    for r in _rows(zf, "trips.txt"):
        tid = r["trip_id"]
        if tid not in first:
            continue
        route = routes.get(r["route_id"])
        headsign = tidy_headsign(r.get("trip_headsign") or "")
        if not headsign:
            headsign = tidy_headsign(stop_names.get(last[tid][1], ""))
        if not headsign and route:
            headsign = route.long_name
        trips[tid] = Trip(
            trip_id=tid,
            route_id=r["route_id"],
            service_id=r["service_id"],
            headsign=headsign,
            block_id=(r.get("block_id") or "").strip(),
            origin_stop_sequence=first[tid][0],
            origin_departure_secs=first[tid][1],
            last_stop_sequence=last[tid][0],
        )
    for stop_id in visits:
        visits[stop_id] = [v for v in visits[stop_id] if v.trip_id in trips]

    services_by_date: dict[str, set[str]] = {}
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for r in _rows(zf, "calendar.txt"):
        day = dt.datetime.strptime(r["start_date"], "%Y%m%d").date()
        end = dt.datetime.strptime(r["end_date"], "%Y%m%d").date()
        while day <= end:
            if r[weekdays[day.weekday()]] == "1":
                services_by_date.setdefault(day.strftime("%Y%m%d"), set()).add(r["service_id"])
            day += dt.timedelta(days=1)
    for r in _rows(zf, "calendar_dates.txt"):
        services = services_by_date.setdefault(r["date"], set())
        if r["exception_type"] == "1":
            services.add(r["service_id"])
        else:
            services.discard(r["service_id"])

    log.info(
        "static index built: %d trips, %s visits, service dates %s..%s",
        len(trips),
        {s: len(v) for s, v in visits.items()},
        min(services_by_date, default="?"),
        max(services_by_date, default="?"),
    )
    return StaticIndex(
        tz=tz,
        stop_ids_by_code=stop_ids_by_code,
        stop_names=stop_names,
        routes=routes,
        trips=trips,
        visits=visits,
        services_by_date=services_by_date,
        version=version,
        missing_stop_codes=missing,
    )


class StaticFeed:
    """Keeps a local copy of the static zip up to date, and the index built from it."""

    def __init__(self, url: str, data_dir: Path, stop_codes: list[str]):
        self.url = url
        self.stop_codes = stop_codes
        self.zip_path = data_dir / "static" / "gtfs.zip"
        self.meta_path = data_dir / "static" / "meta.json"
        self.index: StaticIndex | None = None
        self.last_check: float = 0
        self.last_error: str | None = None
        self.fetched_at: float = 0  # when the zip behind the current index was downloaded

    def _meta(self) -> dict:
        try:
            return json.loads(self.meta_path.read_text())
        except (OSError, ValueError):
            return {}

    def load_cached(self) -> bool:
        if not self.zip_path.exists():
            return False
        meta = self._meta()
        self.index = build_index(self.zip_path.read_bytes(), self.stop_codes, meta.get("etag", ""))
        self.fetched_at = meta.get("fetched_at", self.zip_path.stat().st_mtime)
        return True

    def refresh(self, force: bool = False) -> bool:
        """Download the zip if it changed. Returns True if a new index was built."""
        self.last_check = time.time()
        meta = self._meta() if self.zip_path.exists() and not force else {}
        headers = {"User-Agent": USER_AGENT}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        try:
            resp = httpx.get(self.url, headers=headers, timeout=60, follow_redirects=True)
            if resp.status_code == 304:
                self.last_error = None
                if self.index is None:
                    return self.load_cached()
                return False
            resp.raise_for_status()
            index = build_index(resp.content, self.stop_codes, resp.headers.get("etag", ""))
        except Exception as e:  # keep serving the old index
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("static feed refresh failed: %s", self.last_error)
            if self.index is None:
                self.load_cached()
            return False

        self.zip_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.zip_path.with_suffix(".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(self.zip_path)
        self.meta_path.write_text(
            json.dumps(
                {
                    "etag": resp.headers.get("etag", ""),
                    "last_modified": resp.headers.get("last-modified", ""),
                    "fetched_at": time.time(),
                }
            )
        )
        self.fetched_at = time.time()
        self.index = index  # atomic swap
        self.last_error = None
        return True
