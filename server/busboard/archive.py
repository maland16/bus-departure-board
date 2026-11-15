"""Logs for debugging and improving the estimates after the fact.

data/
  logs/events-YYYYMMDD.jsonl     one JSON object per line: startup, static rebuilds (with how many trip_ids
                                 survived), feed and publish errors and recoveries, trip-ID match-rate dips
  archive/static/<name>.zip      every static feed version seen (name = fetch time + ETag)
  archive/YYYYMMDD/HHMMSS/       the realtime inputs at one moment (every log_interval_s):
      meta.json                  ts, static version file, feed fetch times
      TripUpdate.pb.gz
      VehiclePosition.pb.gz
                                 (older snapshots also hold ucop-<stop>.html.gz, from the UCOP comparison; ignored)
  busboard.db                    predictions and observed departures (see store.py)

`python -m busboard.accuracy <data dir>` replays the archive through the current code (replay_inputs).
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import re
import shutil
import threading
import time
import sys
from dataclasses import dataclass
from pathlib import Path

from .realtime import carry_over_missing_trips, snapshot_from_raw
from .static_gtfs import build_index

log = logging.getLogger(__name__)


class EventLog:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "logs"
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        now = time.time()
        rec = {"ts": round(now, 3), "time": dt.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
               "event": event, **fields}
        path = self.dir / f"events-{dt.date.fromtimestamp(now):%Y%m%d}.jsonl"
        try:
            with self._lock:
                self.dir.mkdir(parents=True, exist_ok=True)
                with path.open("a") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            log.exception("could not write event log")

    def prune(self, keep_days: int) -> None:
        _prune_dated(self.dir.glob("events-*.jsonl"), keep_days, lambda p: p.stem.split("-", 1)[1])


def _prune_dated(paths, keep_days: int, date_of) -> None:
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).strftime("%Y%m%d")
    for p in paths:
        try:
            if date_of(p) < cutoff:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        except (OSError, IndexError):
            pass


@dataclass
class ArchivedSnapshot:
    path: Path
    meta: dict

    @property
    def ts(self) -> int:
        return self.meta["ts"]

    def read(self, name: str) -> bytes | None:
        p = self.path / f"{name}.gz"
        return gzip.decompress(p.read_bytes()) if p.exists() else None


class InputArchive:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "archive"
        self.static_dir = self.root / "static"
        self.last_saved: float | None = None
        self._size: int | None = None  # bytes, measured by measure() and kept up to date by save_snapshot()

    # ---- static versions
    def static_name(self, version: str, fetched_at: float) -> str:
        tag = re.sub(r"[^A-Za-z0-9_-]", "", version)[:40] or "noetag"
        return f"{dt.datetime.fromtimestamp(fetched_at):%Y%m%d-%H%M%S}_{tag}.zip"

    def save_static(self, zip_path: Path, version: str, fetched_at: float, keep: int) -> str:
        self.static_dir.mkdir(parents=True, exist_ok=True)
        tag = re.sub(r"[^A-Za-z0-9_-]", "", version)[:40] or "noetag"
        existing = sorted(self.static_dir.glob(f"*_{tag}.zip"))
        if existing:
            return existing[-1].name
        name = self.static_name(version, fetched_at)
        shutil.copyfile(zip_path, self.static_dir / name)
        for old in sorted(self.static_dir.glob("*.zip"))[:-keep]:
            old.unlink(missing_ok=True)
        return name

    # ---- realtime inputs
    def save_snapshot(self, ts: float, meta: dict, files: dict[str, bytes | None]) -> Path:
        t = dt.datetime.fromtimestamp(ts)
        path = self.root / f"{t:%Y%m%d}" / f"{t:%H%M%S}"
        path.mkdir(parents=True, exist_ok=True)
        written = 0
        for name, data in files.items():
            if data is not None:
                packed = gzip.compress(data, compresslevel=6)
                (path / f"{name}.gz").write_bytes(packed)
                written += len(packed)
        text = json.dumps(meta, default=str)
        (path / "meta.json").write_text(text)
        self.last_saved = ts
        if self._size is not None:
            self._size += written + len(text)
        return path

    def days(self) -> list[str]:
        return sorted(p.name for p in self.root.glob("[0-9]" * 8) if p.is_dir())

    def measure(self) -> int:
        """Total bytes under archive/ (walks every file: run it off the request path)."""
        self._size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()) if self.root.exists() else 0
        return self._size

    @property
    def size_bytes(self) -> int | None:
        return self._size

    def prune(self, keep_days: int) -> None:
        _prune_dated((p for p in self.root.glob("[0-9]" * 8) if p.is_dir()), keep_days, lambda p: p.name)

    def snapshots(self, since: str | None = None, until: str | None = None):
        """Archived snapshots in time order. since/until: YYYYMMDD[-HHMMSS], inclusive."""
        for day in sorted(p for p in self.root.glob("[0-9]" * 8) if p.is_dir()):
            for snap in sorted(p for p in day.iterdir() if p.is_dir()):
                key = f"{day.name}-{snap.name}"
                if (since and key < since) or (until and key[: len(until)] > until):
                    continue
                try:
                    meta = json.loads((snap / "meta.json").read_text())
                except (OSError, ValueError):
                    continue
                yield ArchivedSnapshot(snap, meta)


def replay_inputs(archive: InputArchive, cfg, since=None, until=None, stops_filter=()):
    """Yield (snapshot, static index, realtime snapshot, stop codes) for each archived snapshot, in order.

    Every configured stop (filtered by stops_filter) is evaluated: predictions only need the realtime
    feeds, so stops added to the config later can be evaluated on older archives too.
    """
    stops = [s for s in cfg.stops if not stops_filter or s in set(stops_filter)]
    indexes: dict = {}
    # The server polls every 30 s but archives every log_interval_s, so bridge one missing snapshot
    hold_s = cfg.realtime_hold_missing_s + cfg.log_interval_s if cfg.realtime_hold_missing_s else 0
    last_seen: dict = {}
    for snap in archive.snapshots(since, until):
        static_name = snap.meta.get("static")
        if not static_name:
            print(f"skipping {snap.path}: no static version recorded", file=sys.stderr)
            continue
        if static_name not in indexes:
            zip_path = archive.static_dir / static_name
            if not zip_path.exists():
                print(f"skipping {snap.path}: static version {static_name} not archived", file=sys.stderr)
                continue
            print(f"building index for {static_name}...", file=sys.stderr)
            indexes.clear()  # one at a time keeps memory down
            indexes[static_name] = build_index(zip_path.read_bytes(), list(cfg.stops), snap.meta.get("static_version", ""))
        index = indexes[static_name]
        rt = snapshot_from_raw(snap.read("TripUpdate.pb"), snap.read("VehiclePosition.pb"),
                               snap.meta.get("trip_updates_fetched_at", 0))
        if hold_s:
            rt.trips = carry_over_missing_trips(rt.trips, last_seen, snap.ts, hold_s)
        yield snap, index, rt, [c for c in stops if index.stop_id_for(c)]
