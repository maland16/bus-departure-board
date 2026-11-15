"""The prediction log: what the server predicted for each stop, and when buses actually left (SQLite).

data/busboard.db
  predictions           every log_interval_s, each stop's departures up to log_ahead_s ahead, as served
                        (est includes the drift correction; est - drift_s is the raw estimate), with the
                        Departure's debug fields. `shown`: the row was in the published payload.
  observed_departures   when buses actually left the watched stops, from vehicle positions (observe.py)

Until 2026-11 this was compare.db, which also compared every row with UCOP's predictions. On first
start the old file is renamed and our side of its comparison rows is kept as predictions.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from .departures import Departure

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  ts INTEGER NOT NULL, stop_code TEXT NOT NULL, service_date TEXT NOT NULL, trip_id TEXT NOT NULL,
  stop_sequence INTEGER, route TEXT, sched INTEGER, est INTEGER, src INTEGER, shown INTEGER,
  drift_s INTEGER, timepoint_wait_s INTEGER, held_at_timepoint INTEGER, stale_vehicle INTEGER,
  from_vehicle INTEGER, block_delay_s INTEGER, vehicle_id TEXT, vehicle_age_s INTEGER, rt_updates INTEGER,
  origin_delay_s INTEGER, ref_stop_sequence INTEGER
);
CREATE INDEX IF NOT EXISTS predictions_ts ON predictions(stop_code, ts);
CREATE INDEX IF NOT EXISTS predictions_trip ON predictions(service_date, trip_id);
CREATE TABLE IF NOT EXISTS observed_departures (
  service_date TEXT NOT NULL, trip_id TEXT NOT NULL, stop_code TEXT NOT NULL, stop_sequence INTEGER NOT NULL,
  stop_id TEXT, route TEXT, sched INTEGER, departed_after INTEGER, departed_by INTEGER NOT NULL,
  vehicle_id TEXT, seen_at_stop INTEGER,
  PRIMARY KEY (service_date, trip_id, stop_code, stop_sequence)
);
CREATE INDEX IF NOT EXISTS observed_by ON observed_departures(departed_by);
"""

# Departure fields stored as-is (the rest of the columns are filled in by record_predictions)
DEPARTURE_COLUMNS = [
    "service_date", "trip_id", "stop_sequence", "route", "sched", "est", "src", "drift_s", "timepoint_wait_s",
    "held_at_timepoint", "stale_vehicle", "from_vehicle", "block_delay_s", "vehicle_id", "vehicle_age_s",
    "rt_updates", "origin_delay_s", "ref_stop_sequence",
]
COLUMNS = ["ts", "stop_code", "shown", *DEPARTURE_COLUMNS]

# Our side of compare.db's comparison rows. Rows only UCOP listed have no estimate of ours; trips
# ending at the stop were never on the board.
MIGRATE_ROWS = """
INSERT INTO predictions (ts, stop_code, service_date, trip_id, stop_sequence, route, sched, est, src,
  drift_s, held_at_timepoint, stale_vehicle, from_vehicle, vehicle_id, vehicle_age_s, rt_updates,
  origin_delay_s, ref_stop_sequence)
SELECT ts, stop_code, service_date, trip_id, stop_sequence, route, our_sched, our_est, our_src,
  {drift_s}, {held}, stale_vehicle, {from_vehicle}, {vehicle_id}, {vehicle_age_s}, {rt_updates},
  {origin_delay_s}, {ref_stop_sequence}
FROM rows
WHERE our_est IS NOT NULL AND service_date IS NOT NULL AND trip_id IS NOT NULL
  AND category NOT IN ('ucop_only', 'ucop_departed', 'ours_terminating')
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.with_name("compare.db")
        if not path.exists() and old.exists():
            for suffix in ("", "-wal", "-shm"):
                src = old.with_name(old.name + suffix)
                if src.exists():
                    src.rename(path.with_name(path.name + suffix))
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")  # WAL keeps this safe across power cuts
            self._db.execute("PRAGMA journal_size_limit = 67108864")  # don't leave a huge WAL behind (64 MB)
            self._db.executescript(SCHEMA)
            self._db.commit()

    def migrate_legacy(self) -> dict | None:
        """Keep our side of compare.db's UCOP comparison rows as predictions, then drop the comparison
        tables. Slow once (minutes on the Pi for two weeks of rows), a no-op after. Returns counts if it ran."""
        with self._lock:
            return self._migrate_compare_rows()

    def _migrate_compare_rows(self) -> dict | None:
        tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "rows" not in tables:
            return None
        start = time.monotonic()
        log.info("migrating compare.db comparison rows to predictions; this takes a while once")
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(rows)")}

        def col(name, default="NULL"):
            return name if name in have else default

        old_rows = self._db.execute("SELECT COUNT(*) FROM rows").fetchone()[0]
        with self._db:
            kept = self._db.execute(MIGRATE_ROWS.format(
                drift_s=col("drift_s", "0"), held=col("held_at_timepoint", "0"), from_vehicle=col("from_vehicle", "0"),
                vehicle_id=col("vehicle_id"), vehicle_age_s=col("vehicle_age_s"), rt_updates=col("rt_updates"),
                origin_delay_s=col("origin_delay_s"), ref_stop_sequence=col("ref_stop_sequence"),
            )).rowcount
            self._db.execute("DROP TABLE rows")
            self._db.execute("DROP TABLE IF EXISTS snapshots")
        self._db.execute("VACUUM")
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the copy went through the WAL: give the space back
        took = round(time.monotonic() - start, 1)
        log.info("migrated %d of %d comparison rows in %.1f s", kept, old_rows, took)
        return {"comparison_rows": old_rows, "predictions_kept": kept, "took_s": took}

    # ---- writing
    def record_predictions(self, stop_code: str, ts: int, departures: list[Departure], shown: set[tuple]) -> int:
        """shown: (trip_id, stop_sequence) of the departures in the published payload."""
        values = []
        for d in departures:
            row = [ts, stop_code, int((d.trip_id, d.stop_sequence) in shown)]
            for c in DEPARTURE_COLUMNS:
                x = getattr(d, c)
                row.append(int(x) if isinstance(x, bool) else x)
            values.append(row)
        if values:
            with self._lock, self._db:
                self._db.executemany(
                    f"INSERT INTO predictions ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})", values)
        return len(values)

    def record_departures(self, departures: list, stop_codes: dict[str, str], routes: dict[str, str]) -> None:
        """departures: ObservedDeparture list; stop_codes: stop_id -> code; routes: trip_id -> route."""
        if not departures:
            return
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO observed_departures (service_date, trip_id, stop_code, stop_sequence, stop_id,"
                " route, sched, departed_after, departed_by, vehicle_id, seen_at_stop) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(o.service_date, o.trip_id, stop_codes.get(o.stop_id, o.stop_id), o.stop_sequence, o.stop_id,
                  routes.get(o.trip_id), o.sched, o.departed_after, o.departed_by, o.vehicle_id, int(o.seen_at_stop))
                 for o in departures],
            )

    def prune(self, retention_days: int) -> None:
        cutoff = int(time.time()) - retention_days * 86400
        with self._lock, self._db:
            self._db.execute("DELETE FROM predictions WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM observed_departures WHERE departed_by < ?", (cutoff,))

    # ---- reading
    def accuracy(self, since: int, lo_s: int = 300, hi_s: int = 1200, max_uncertainty_s: int = 90) -> dict[str, dict]:
        """Per stop: error of predictions made lo_s..hi_s before the observed departure. Each logged
        prediction counts once."""
        q = """
        SELECT p.stop_code, COUNT(*) AS n, AVG(ABS(p.est - a)) AS mae, AVG(p.est - a) AS bias,
               AVG(ABS(p.est - a) <= 120) AS w2
        FROM (SELECT p.stop_code, p.est, p.ts, (o.departed_after + o.departed_by) / 2 AS a
              FROM predictions p JOIN observed_departures o
                ON o.service_date = p.service_date AND o.trip_id = p.trip_id AND o.stop_code = p.stop_code
               AND o.stop_sequence = p.stop_sequence
              WHERE p.ts >= :since AND o.departed_after IS NOT NULL AND o.departed_by - o.departed_after <= :unc * 2
             ) p
        WHERE a - p.ts >= :lo AND a - p.ts < :hi
        GROUP BY p.stop_code
        """
        with self._lock:
            rows = self._db.execute(q, {"since": since, "unc": max_uncertainty_s, "lo": lo_s, "hi": hi_s}).fetchall()
        return {r["stop_code"]: dict(r) for r in rows}

    def drift_training(self, since: int) -> list[dict]:
        """Past raw predictions (logged estimate minus the drift applied) with their observed departure,
        to warm-start the drift model after a restart."""
        with self._lock:
            return [dict(r) for r in self._db.execute(
                """SELECT p.ts, p.service_date, p.trip_id, p.stop_code, p.stop_sequence, p.src,
                          p.est - COALESCE(p.drift_s, 0) AS raw_est,
                          (o.departed_after + o.departed_by) / 2 AS actual
                   FROM predictions p JOIN observed_departures o
                     ON o.service_date = p.service_date AND o.trip_id = p.trip_id AND o.stop_code = p.stop_code
                    AND o.stop_sequence = p.stop_sequence
                   WHERE p.ts >= ? AND p.src IN (1, 2) AND o.departed_after IS NOT NULL
                     AND o.departed_by - o.departed_after <= 180""", (since,))]

    def counts_since(self, since: int) -> dict[str, dict]:
        """Per stop: predictions logged and departures observed since `since`."""
        out: dict[str, dict] = {}
        with self._lock:
            for r in self._db.execute("SELECT stop_code, COUNT(*) FROM predictions WHERE ts >= ? GROUP BY 1", (since,)):
                out.setdefault(r[0], {"predictions": 0, "observed": 0})["predictions"] = r[1]
            for r in self._db.execute(
                    "SELECT stop_code, COUNT(*) FROM observed_departures WHERE departed_by >= ? GROUP BY 1", (since,)):
                out.setdefault(r[0], {"predictions": 0, "observed": 0})["observed"] = r[1]
        return out

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
