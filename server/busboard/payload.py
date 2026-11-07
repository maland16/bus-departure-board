"""The compact JSON the board will eventually download instead of a bitmap (~0.5 KB)."""

from __future__ import annotations

from .departures import Departure

PAYLOAD_VERSION = 1


def build_payload(stop_code: str, stop_name: str, departures: list[Departure], now: int) -> dict:
    return {
        "v": PAYLOAD_VERSION,
        "now": now,
        "stop": stop_code,
        "name": stop_name,
        "deps": [
            {
                "r": d.route,
                "h": d.headsign,
                "t": d.est,  # estimated departure, epoch seconds
                "s": d.sched,  # scheduled departure
                "src": d.src,  # 2 = predicted for this stop, 1 = estimated from first stop, 0 = schedule
            }
            for d in departures
        ],
        "alerts": [],
    }
