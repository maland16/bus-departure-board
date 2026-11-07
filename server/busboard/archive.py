"""Logs for debugging and improving the GTFS handling after the fact.

data/
  logs/events-YYYYMMDD.jsonl     one JSON object per line: static rebuilds (with how many trip_ids
                                 survived), feed errors, trip-ID match-rate dips, startup
  archive/static/<name>.zip      every static feed version seen (name = fetch time + ETag)
  archive/YYYYMMDD/HHMMSS/       the exact inputs of one comparison:
      meta.json                  ts, static version file, stops, UCOP fetch times/errors
      TripUpdate.pb.gz
      VehiclePosition.pb.gz
      ucop-<stop>.html.gz
  compare.db                     comparison results (see compare.py)

`python -m busboard.replay <data dir>` re-runs archived comparisons through the current code.
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
from dataclasses import dataclass
from pathlib import Path

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

    def ucop_html(self, stop_code: str) -> str | None:
        data = self.read(f"ucop-{stop_code}.html")
        return data.decode("utf-8", "replace") if data is not None else None


class InputArchive:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "archive"
        self.static_dir = self.root / "static"

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

    # ---- comparison inputs
    def save_snapshot(self, ts: float, meta: dict, files: dict[str, bytes]) -> Path:
        t = dt.datetime.fromtimestamp(ts)
        path = self.root / f"{t:%Y%m%d}" / f"{t:%H%M%S}"
        path.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            if data is not None:
                (path / f"{name}.gz").write_bytes(gzip.compress(data, compresslevel=6))
        (path / "meta.json").write_text(json.dumps(meta, default=str))
        return path

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
