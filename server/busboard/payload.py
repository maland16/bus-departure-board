"""The compact JSON the boards fetch instead of a bitmap (~1 KB for 12 rows).

Schema v1 (new fields may be added; boards should ignore ones they don't know):
  v       1
  now     when this was generated (epoch s). Boards should treat old payloads as stale.
  tz      the stop's UTC offset at `now` in seconds (-18000 = EST), for showing clock times
  live    false when GP Metro's realtime feed is down: every time is the schedule
  stop    stop code, as on the sign
  name    stop name
  deps    upcoming departures, soonest first (trips ending at this stop and canceled ones left out)
          r    route, e.g. "4", "24A", "BRZ"
          h    headsign (destination)
          t    estimated departure, epoch s
          s    scheduled departure, epoch s
          src  2 = predicted for this stop, 1 = estimated from another stop, 0 = schedule only
  alerts  reserved (always empty: GP Metro's alert feed has been empty)
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .departures import Departure

PAYLOAD_VERSION = 1


def build_payload(stop_code: str, stop_name: str, departures: list[Departure], now: int, tz: ZoneInfo,
                  live: bool = True) -> dict:
    offset = dt.datetime.fromtimestamp(now, tz).utcoffset()
    return {
        "v": PAYLOAD_VERSION,
        "now": now,
        "tz": int(offset.total_seconds()) if offset is not None else 0,
        "live": live,
        "stop": stop_code,
        "name": stop_name,
        "deps": [{"r": d.route, "h": d.headsign, "t": d.est, "s": d.sched, "src": d.src} for d in departures],
        "alerts": [],
    }
