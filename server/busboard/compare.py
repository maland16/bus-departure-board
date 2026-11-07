"""Joining our departures with UCOP's on (service date, trip_id), and keeping a history in SQLite."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .departures import MAX_LATENESS_S, Departure, compute_departures
from .ucop import UcopArrival

# Categories
MATCH = "match"
TIME_DIFF = "time_diff"
OURS_ONLY = "ours_only"
UCOP_ONLY = "ucop_only"
UCOP_DEPARTED = "ucop_departed"  # UCOP keeps departed buses listed; we drop them. Not counted.
# A trip ending at this stop that only we list. UCOP (which caps lists at 40) leaves some of these
# out, mostly at hubs, and our board never shows them. Not counted.
OURS_TERMINATING = "ours_terminating"
NOT_COUNTED = (UCOP_DEPARTED, OURS_TERMINATING)


@dataclass
class CompareRow:
    service_date: str
    trip_id: str
    route: str
    category: str
    visit: int = 0  # nth visit of this trip to the stop (some trips loop past a stop twice)
    our_sched: int | None = None
    our_est: int | None = None
    our_src: int | None = None
    stale_vehicle: bool = False
    stale_block: bool = False
    our_headsign: str | None = None
    stop_sequence: int | None = None
    vehicle_id: str | None = None
    vehicle_age_s: int | None = None
    rt_updates: int | None = None
    origin_delay_s: int | None = None
    timepoint: bool | None = None
    ucop_sched: int | None = None
    ucop_est: int | None = None
    ucop_status: str | None = None
    ucop_status_text: str | None = None
    ucop_countdown: str | None = None
    ucop_headsign: str | None = None

    @property
    def diff_s(self) -> int | None:
        if self.our_est is None or self.ucop_est is None:
            return None
        return self.our_est - self.ucop_est

    @property
    def headsign_differs(self) -> bool:
        return (self.our_headsign is not None and self.ucop_headsign is not None
                and _norm(self.our_headsign) != _norm(self.ucop_headsign))


def _norm(h: str | None) -> str:
    return " ".join((h or "").lower().replace("+", "&").split())


def _keyed(items, sched_of):
    """{(service_date, trip_id, n): item}, numbering repeat visits of a trip in schedule order.
    UCOP's data-key can't tell a looping trip's two visits apart, so pair them up by order."""
    groups: dict[tuple[str, str], list] = {}
    for it in items:
        groups.setdefault((it.service_date, it.trip_id), []).append(it)
    out = {}
    for (date, trip), its in groups.items():
        for n, it in enumerate(sorted(its, key=lambda x: sched_of(x) or 0)):
            out[(date, trip, n)] = it
    return out


def compare(
    ours: list[Departure], ucop: list[UcopArrival], now: int, *, tolerance_s: int, past_grace_s: int, window_ahead_s: int
) -> list[CompareRow]:
    """`ours` should be computed without dropping past departures, so a trip we place in the
    past (e.g. a stale vehicle reset to its schedule) but UCOP still shows as late counts as a
    time difference on a trip both know about, not as missing."""
    theirs = _keyed(ucop, lambda a: a.sched)
    mine = _keyed(ours, lambda d: d.sched)
    # Only compare the stretch of time both lists cover; UCOP's list is shorter than ours
    ucop_until = max((a.est for a in ucop if a.est), default=now)
    our_until = now + window_ahead_s
    rows = []
    for key in sorted(set(theirs) | set(mine)):
        d, a = mine.get(key), theirs.get(key)
        row = CompareRow(service_date=key[0], trip_id=key[1], visit=key[2],
                         route=(d.route if d else a.route), category="")
        if d:
            row.our_sched, row.our_est, row.our_src = d.sched, d.est, d.src
            row.stale_vehicle, row.stale_block = d.stale_vehicle, d.stale_block
            row.our_headsign = "ends here" if d.ends_here else d.headsign  # UCOP's wording
            row.stop_sequence, row.vehicle_id, row.vehicle_age_s = d.stop_sequence, d.vehicle_id, d.vehicle_age_s
            row.rt_updates, row.origin_delay_s, row.timepoint = d.rt_updates, d.origin_delay_s, d.timepoint
        if a:
            row.ucop_sched, row.ucop_est, row.ucop_status, row.ucop_headsign = a.sched, a.est, a.status, a.headsign
            row.ucop_status_text, row.ucop_countdown = a.status_text, a.countdown
        if d and a:
            if a.departed:
                row.category = UCOP_DEPARTED
            else:
                row.category = MATCH if abs(row.diff_s) <= tolerance_s else TIME_DIFF
        elif d:
            if d.est > ucop_until or d.est < now - past_grace_s:
                continue  # beyond the end of UCOP's list, or already gone (and not on the board)
            row.category = OURS_TERMINATING if d.ends_here else OURS_ONLY
        else:
            if a.departed:
                row.category = UCOP_DEPARTED
            elif a.est is None or not (now - past_grace_s <= a.est <= our_until):
                continue  # outside our window
            else:
                row.category = UCOP_ONLY
        rows.append(row)
    rows.sort(key=lambda r: r.our_est or r.ucop_est or 0)
    return rows


def compare_stop(index, realtime, ucop_arrivals: list[UcopArrival], stop_code: str, now: int, cfg
                 ) -> tuple[list[Departure], list[CompareRow]]:
    """Compute our departures for one stop and compare them with UCOP's. Used by the server and replay."""
    ours = compute_departures(index, realtime, stop_code, now, window_ahead_s=cfg.window_ahead_s,
                              past_grace_s=MAX_LATENESS_S, stale_vehicle_s=cfg.stale_vehicle_s)
    rows = compare(ours, ucop_arrivals, now, tolerance_s=cfg.match_tolerance_s,
                   past_grace_s=cfg.past_grace_s, window_ahead_s=cfg.window_ahead_s)
    return ours, rows


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, stop_code TEXT NOT NULL,
  ucop_count INTEGER, ours_count INTEGER, ucop_error TEXT
);
CREATE TABLE IF NOT EXISTS rows (
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, stop_code TEXT NOT NULL,
  service_date TEXT, trip_id TEXT, route TEXT, category TEXT,
  our_sched INTEGER, our_est INTEGER, our_src INTEGER, stale_vehicle INTEGER, stale_block INTEGER,
  ucop_sched INTEGER, ucop_est INTEGER, ucop_status TEXT, diff_s INTEGER,
  our_headsign TEXT, ucop_headsign TEXT, headsign_differs INTEGER
);
CREATE INDEX IF NOT EXISTS rows_ts ON rows(stop_code, ts);
CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots(stop_code, ts);
"""

# Columns added after the first deployment; added to existing databases on startup
ADDED_COLUMNS = {
    "rows": {
        "visit": "INTEGER", "stop_sequence": "INTEGER", "vehicle_id": "TEXT", "vehicle_age_s": "INTEGER",
        "rt_updates": "INTEGER", "origin_delay_s": "INTEGER", "ucop_status_text": "TEXT", "ucop_countdown": "TEXT",
        "timepoint": "INTEGER",
    },
    "snapshots": {
        "archive": "TEXT",  # archive/YYYYMMDD/HHMMSS directory holding this snapshot's inputs
    },
}
ROW_COLUMNS = [
    "snapshot_id", "ts", "stop_code", "service_date", "trip_id", "visit", "route", "category",
    "our_sched", "our_est", "our_src", "stale_vehicle", "stale_block", "stop_sequence", "vehicle_id",
    "vehicle_age_s", "rt_updates", "origin_delay_s", "timepoint", "ucop_sched", "ucop_est", "ucop_status",
    "ucop_status_text", "ucop_countdown", "diff_s", "our_headsign", "ucop_headsign", "headsign_differs",
]


class CompareStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(SCHEMA)
            for table, cols in ADDED_COLUMNS.items():
                have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
                for col, typ in cols.items():
                    if col not in have:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            self._db.commit()

    def record(self, stop_code: str, ts: int, rows: list[CompareRow], ucop_count: int, ours_count: int,
               ucop_error: str | None, archive: str | None = None) -> None:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO snapshots (ts, stop_code, ucop_count, ours_count, ucop_error, archive) VALUES (?,?,?,?,?,?)",
                (ts, stop_code, ucop_count, ours_count, ucop_error, archive),
            )
            sid = cur.lastrowid
            values = []
            for r in rows:
                v = {"snapshot_id": sid, "ts": ts, "stop_code": stop_code, "diff_s": r.diff_s,
                     "headsign_differs": int(r.headsign_differs)}
                for c in ROW_COLUMNS:
                    if c not in v:
                        x = getattr(r, c)
                        v[c] = int(x) if isinstance(x, bool) else x
                values.append([v[c] for c in ROW_COLUMNS])
            self._db.executemany(
                f"INSERT INTO rows ({','.join(ROW_COLUMNS)}) VALUES ({','.join('?' * len(ROW_COLUMNS))})", values
            )

    def prune(self, retention_days: int) -> None:
        cutoff = int(time.time()) - retention_days * 86400
        with self._lock, self._db:
            self._db.execute("DELETE FROM rows WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))

    def summary(self, stop_code: str, since: int, near_term_s: int = 900) -> dict:
        """Counts over the period. 'near_term' is limited to departures due within 15 min,
        which is what matters most on the board."""
        q = """
        SELECT
          COUNT(*) AS rows,
          SUM(category='match') AS match,
          SUM(category='time_diff') AS time_diff,
          SUM(category='ours_only') AS ours_only,
          SUM(category='ucop_only') AS ucop_only,
          SUM(category='time_diff' AND stale_vehicle) AS time_diff_stale_vehicle,
          SUM(category='time_diff' AND stale_block AND NOT stale_vehicle) AS time_diff_stale_block,
          SUM(category='time_diff' AND our_src=2 AND NOT stale_vehicle AND NOT stale_block) AS time_diff_src2,
          SUM(category='time_diff' AND our_src=1 AND NOT stale_vehicle AND NOT stale_block) AS time_diff_src1,
          SUM(category='time_diff' AND our_src=0 AND NOT stale_vehicle AND NOT stale_block) AS time_diff_src0,
          SUM(category IN ('match','time_diff') AND our_src=2) AS src2,
          SUM(category IN ('match','time_diff') AND our_src=1) AS src1,
          SUM(category IN ('match','time_diff') AND our_src=0) AS src0,
          SUM(headsign_differs) AS headsign_differs,
          COUNT(DISTINCT trip_id) AS trips,
          (SELECT COUNT(*) FROM snapshots WHERE stop_code=:stop AND ts>=:since) AS snapshots
        FROM rows WHERE stop_code=:stop AND ts>=:since AND category NOT IN ('ucop_departed', 'ours_terminating') {extra}
        """
        with self._lock:
            all_rows = dict(self._db.execute(q.format(extra=""), {"stop": stop_code, "since": since}).fetchone())
            near = dict(self._db.execute(
                q.format(extra="AND COALESCE(our_est, ucop_est) - ts <= :near"),
                {"stop": stop_code, "since": since, "near": near_term_s},
            ).fetchone())
        return {"all": all_rows, "near_term": near}

    def latest(self, stop_code: str) -> tuple[dict | None, list[dict]]:
        with self._lock:
            snap = self._db.execute(
                "SELECT * FROM snapshots WHERE stop_code=? ORDER BY ts DESC LIMIT 1", (stop_code,)
            ).fetchone()
            if not snap:
                return None, []
            rows = self._db.execute(
                "SELECT * FROM rows WHERE snapshot_id=? ORDER BY COALESCE(our_est, ucop_est)", (snap["id"],)
            ).fetchall()
        return dict(snap), [dict(r) for r in rows]

    def recent_mismatches(self, stop_code: str, since: int, limit: int = 50) -> list[dict]:
        """The latest comparison of each trip that disagreed, most recent first."""
        with self._lock:
            rows = self._db.execute(
                """SELECT * FROM rows WHERE rowid IN (
                     SELECT MAX(rowid) FROM rows
                     WHERE stop_code=? AND ts>=? AND category IN ('time_diff','ours_only','ucop_only')
                     GROUP BY service_date, trip_id)
                   ORDER BY ts DESC LIMIT ?""",
                (stop_code, since, limit),
            ).fetchall()
        return [dict(r) for r in rows]
