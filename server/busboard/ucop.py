"""Fetching UCOP's stop page and display image, to compare our departures against.

Each departure on https://transit.ucop.me/stops/<code>/ is an <a class="arrival"> carrying
data-key="<YYYYMMDD service date>:<GTFS trip_id>:<stop_id>". Its times are clock strings like
"7:45 pm". When a prediction differs from the schedule, the scheduled time appears in
.arrival-time-superseded, followed by the estimate. Otherwise there's a single time, with an
"On Time" status for realtime trips and no status for schedule-only ones. Buses that have
left stay listed with data-status="idle" ("Departed").
"""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx

from .static_gtfs import USER_AGENT


@dataclass
class UcopArrival:
    service_date: str
    trip_id: str
    stop_id: str
    route: str
    headsign: str
    sched: int | None
    est: int | None
    status: str | None  # data-status: late / early / ok / idle, None = schedule only
    status_text: str
    countdown: str

    @property
    def departed(self) -> bool:
        return self.status == "idle"

    @property
    def realtime(self) -> bool:
        return self.status in ("late", "early", "ok")


@dataclass
class UcopSnapshot:
    fetched_at: float = 0
    arrivals: list[UcopArrival] = field(default_factory=list)
    html: bytes | None = field(default=None, repr=False)  # raw page, archived for replay
    error: str | None = None


_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([ap])\.?m\.?\s*$", re.I)


def clock_to_epoch(text: str, service_date: str, near: float, tz: ZoneInfo) -> int | None:
    """'7:45 pm' -> epoch, picking whichever day around the service date is closest to `near`."""
    m = _CLOCK.match(text or "")
    if not m:
        return None
    hour, minute = int(m.group(1)) % 12, int(m.group(2))
    if m.group(3).lower() == "p":
        hour += 12
    base = dt.datetime.strptime(service_date, "%Y%m%d").date()
    candidates = []
    for offset in (-1, 0, 1):
        d = base + dt.timedelta(days=offset)
        candidates.append(int(dt.datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz).timestamp()))
    return min(candidates, key=lambda c: abs(c - near))


class _ArrivalParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.cur: dict | None = None
        self.field: str | None = None  # which text we're collecting
        self.in_times = False
        self.depth_a = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "arrival" in classes:
            self.cur = {"key": a.get("data-key", ""), "route": "", "headsign": "", "times": [],
                        "superseded": None, "status": None, "status_text": "", "countdown": ""}
            return
        if self.cur is None:
            return
        if tag == "span" and "arrival-route-name" in classes:
            self.field = "route"
        elif tag == "span" and "arrival-headsign" in classes:
            self.field = "headsign"
        elif tag == "p" and "arrival-times" in classes:
            self.in_times = True
        elif tag == "p" and "arrival-countdown" in classes:
            self.field = "countdown"
        elif tag == "span" and self.in_times:
            if "arrival-time-superseded" in classes:
                self.field = "superseded"
                self.cur["superseded"] = ""
            elif "arrival-status" in classes:
                self.field = "status_text"
                self.cur["status"] = a.get("data-status")
            elif not classes:
                self.field = "time"
                self.cur["times"].append("")

    def handle_endtag(self, tag):
        if self.cur is None:
            return
        if tag == "a":
            self.items.append(self.cur)
            self.cur, self.field, self.in_times = None, None, False
        elif tag == "p":
            self.in_times = False
            self.field = None
        elif tag == "span":
            self.field = None

    def handle_data(self, data):
        if self.cur is None or self.field is None:
            return
        if self.field == "time":
            self.cur["times"][-1] += data
        else:
            self.cur[self.field] = (self.cur[self.field] or "") + data


def parse_stop_page(html: str, fetched_at: float, tz: ZoneInfo) -> list[UcopArrival]:
    p = _ArrivalParser()
    p.feed(html)
    out = []
    for it in p.items:
        parts = it["key"].split(":", 2)
        if len(parts) != 3:
            continue
        service_date, trip_id, stop_id = parts
        times = [t.strip() for t in it["times"] if t.strip()]
        est = clock_to_epoch(times[-1], service_date, fetched_at, tz) if times else None
        sched = clock_to_epoch(it["superseded"], service_date, fetched_at, tz) if it["superseded"] else est
        headsign = " ".join(it["headsign"].split())
        out.append(
            UcopArrival(
                service_date=service_date,
                trip_id=trip_id,
                stop_id=stop_id,
                route=it["route"].strip(),
                headsign=headsign[3:] if headsign.lower().startswith("to ") else headsign,
                sched=sched,
                est=est,
                status=it["status"],
                status_text=" ".join(it["status_text"].split()),
                countdown=" ".join(it["countdown"].split()),
            )
        )
    return out


class UcopClient:
    def __init__(self, base_url: str, display_name: str, tz: ZoneInfo):
        self.base_url = base_url
        self.display_name = display_name
        self.tz = tz
        self._client = httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    def fetch_page(self, stop_code: str) -> UcopSnapshot:
        snap = UcopSnapshot(fetched_at=time.time())
        try:
            resp = self._client.get(f"{self.base_url}/stops/{stop_code}/")
            snap.html = resp.content
            resp.raise_for_status()
            snap.arrivals = parse_stop_page(resp.text, snap.fetched_at, self.tz)
        except Exception as e:
            snap.error = f"page: {type(e).__name__}: {e}"
        return snap

    def fetch_bmp(self, stop_code: str) -> bytes:
        resp = self._client.get(f"{self.base_url}/stops/{stop_code}/display/{self.display_name}/display.bmp")
        resp.raise_for_status()
        return resp.content
