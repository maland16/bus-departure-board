"""Joining our departures with UCOP's on (service date, trip_id), and keeping a history in SQLite."""

from __future__ import annotations

import dataclasses
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
# UCOP's page couldn't be fetched (or the stop has none): our departures are still logged, so
# they can be scored against observed departures. Not counted in the comparison.
NO_UCOP = "no_ucop"
NOT_COUNTED = (UCOP_DEPARTED, OURS_TERMINATING, NO_UCOP)

# Why a time_diff happened. The first four are disagreements where the evidence says UCOP is
# showing something stale or impossible; whatever is left (by our source) is worth investigating.
DIFF_REASONS = {
    "stale_vehicle": "we ignored a ghost bus's prediction",
    "stale_block": "an earlier trip on the same block has a ghost bus",
    "held_at_timepoint": "we don't show buses leaving a timepoint early (they wait)",
    "gone_from_feed": "the trip left the realtime feed; UCOP still shows its last prediction",
    "drift_correction": "our learned drift correction moved the time; uncorrected, it matched UCOP",
    "src2": "other: predicted for this stop",
    "src1": "other: estimated from another stop",
    "src0": "other: schedule only",
}
EXPECTED_REASONS = ("stale_vehicle", "stale_block", "held_at_timepoint", "gone_from_feed", "drift_correction")
# Same precedence as CompareRow.diff_reason, for rows stored before that column existed
DIFF_REASON_SQL = """COALESCE(diff_reason, CASE WHEN stale_vehicle THEN 'stale_vehicle'
  WHEN stale_block THEN 'stale_block' ELSE 'src' || our_src END)"""


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
    ref_stop_sequence: int | None = None
    held_at_timepoint: bool = False
    drift_s: int = 0
    from_vehicle: bool = False
    raw_matches_ucop: bool = False  # before the drift correction, within tolerance of UCOP
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
    def diff_reason(self) -> str | None:
        if self.category != TIME_DIFF:
            return None
        if self.stale_vehicle:
            return "stale_vehicle"
        if self.stale_block:
            return "stale_block"
        if self.held_at_timepoint:
            return "held_at_timepoint"
        if self.rt_updates is None and self.ucop_status in ("late", "early", "ok"):
            return "gone_from_feed"
        if self.drift_s and self.raw_matches_ucop:
            return "drift_correction"
        return f"src{self.our_src}"

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
            _fill_ours(row, d)
        if a:
            row.ucop_sched, row.ucop_est, row.ucop_status, row.ucop_headsign = a.sched, a.est, a.status, a.headsign
            row.ucop_status_text, row.ucop_countdown = a.status_text, a.countdown
        if d and a:
            if a.departed:
                row.category = UCOP_DEPARTED
            else:
                row.category = MATCH if abs(row.diff_s) <= tolerance_s else TIME_DIFF
                row.raw_matches_ucop = abs(row.diff_s - row.drift_s) <= tolerance_s
        elif d:
            if d.est > ucop_until or d.est < now:
                # Beyond the end of UCOP's list, or already due: we keep departures up for
                # past_grace_s, UCOP drops them as soon as the bus leaves. Neither is a parsing issue.
                continue
            row.category = OURS_TERMINATING if d.ends_here else OURS_ONLY
        else:
            if a.departed:
                row.category = UCOP_DEPARTED
            elif a.est is None or not (now - past_grace_s <= a.est <= our_until - tolerance_s):
                # Outside our window. The margin at the far end is because UCOP truncates times to
                # the minute: a 5:17:30 departure shows as 5:17, inside a window ending at 5:17:10.
                continue
            else:
                row.category = UCOP_ONLY
        rows.append(row)
    rows.sort(key=lambda r: r.our_est or r.ucop_est or 0)
    return rows


def _fill_ours(row: CompareRow, d: Departure) -> None:
    row.our_sched, row.our_est, row.our_src = d.sched, d.est, d.src
    row.stale_vehicle, row.stale_block = d.stale_vehicle, d.stale_block
    row.our_headsign = "ends here" if d.ends_here else d.headsign  # UCOP's wording
    row.stop_sequence, row.vehicle_id, row.vehicle_age_s = d.stop_sequence, d.vehicle_id, d.vehicle_age_s
    row.rt_updates, row.origin_delay_s, row.timepoint = d.rt_updates, d.origin_delay_s, d.timepoint
    row.ref_stop_sequence, row.held_at_timepoint = d.ref_stop_sequence, d.held_at_timepoint
    row.drift_s, row.from_vehicle = d.drift_s, d.from_vehicle


def no_ucop_rows(ours: list[Departure], now: int, window_ahead_s: int) -> list[CompareRow]:
    rows = []
    for d in ours:
        if now <= d.est <= now + window_ahead_s and not d.ends_here:
            row = CompareRow(service_date=d.service_date, trip_id=d.trip_id, route=d.route, category=NO_UCOP)
            _fill_ours(row, d)
            rows.append(row)
    return rows


def compare_stop(index, realtime, ucop_arrivals: list[UcopArrival] | None, stop_code: str, now: int, cfg,
                 drift=None) -> tuple[list[Departure], list[CompareRow]]:
    """Compute our departures for one stop and compare them with UCOP's (None: no UCOP page, so
    just log ours). With a drift.DriftModel, the raw predictions are recorded for it to learn from
    and the corrected ones (what the boards get) are compared. Used by the server and replay."""
    ours = compute_departures(index, realtime, stop_code, now, window_ahead_s=cfg.window_ahead_s,
                              past_grace_s=MAX_LATENESS_S, stale_vehicle_s=cfg.stale_vehicle_s,
                              block_layover_s=cfg.block_layover_s)
    if drift is not None:
        drift.record(index, ours, now)
        ours = drift.correct(index, [dataclasses.replace(d) for d in ours], now)
    if ucop_arrivals is None:
        return ours, no_ucop_rows(ours, now, cfg.window_ahead_s)
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
-- When buses actually left watched stops, from vehicle positions (see observe.py)
CREATE TABLE IF NOT EXISTS observed_departures (
  service_date TEXT NOT NULL, trip_id TEXT NOT NULL, stop_code TEXT NOT NULL, stop_sequence INTEGER NOT NULL,
  stop_id TEXT, route TEXT, sched INTEGER, departed_after INTEGER, departed_by INTEGER NOT NULL,
  vehicle_id TEXT, seen_at_stop INTEGER,
  PRIMARY KEY (service_date, trip_id, stop_code, stop_sequence)
);
CREATE INDEX IF NOT EXISTS observed_by ON observed_departures(departed_by);
"""

# Columns added after the first deployment; added to existing databases on startup
ADDED_COLUMNS = {
    "rows": {
        "visit": "INTEGER", "stop_sequence": "INTEGER", "vehicle_id": "TEXT", "vehicle_age_s": "INTEGER",
        "rt_updates": "INTEGER", "origin_delay_s": "INTEGER", "ucop_status_text": "TEXT", "ucop_countdown": "TEXT",
        "timepoint": "INTEGER", "ref_stop_sequence": "INTEGER", "held_at_timepoint": "INTEGER",
        "diff_reason": "TEXT", "drift_s": "INTEGER", "from_vehicle": "INTEGER",
    },
    "snapshots": {
        "archive": "TEXT",  # archive/YYYYMMDD/HHMMSS directory holding this snapshot's inputs
    },
}
ROW_COLUMNS = [
    "snapshot_id", "ts", "stop_code", "service_date", "trip_id", "visit", "route", "category",
    "our_sched", "our_est", "our_src", "stale_vehicle", "stale_block", "stop_sequence", "vehicle_id",
    "vehicle_age_s", "rt_updates", "origin_delay_s", "timepoint", "ref_stop_sequence",
    "held_at_timepoint", "diff_reason", "drift_s", "from_vehicle", "ucop_sched", "ucop_est", "ucop_status",
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
            self._db.execute("DELETE FROM rows WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM observed_departures WHERE departed_by < ?", (cutoff,))

    def accuracy(self, stop_code: str, since: int, buckets=((0, 5), (5, 20), (20, 60)), max_uncertainty_s: int = 90
                 ) -> dict[str, dict]:
        """Prediction error against observed departures, for ours and UCOP, by minutes before departure.
        Each comparison snapshot counts once per departure it showed."""
        q = """
        SELECT COUNT(r.our_est) AS n, AVG(ABS(r.our_est - a)) AS mae, AVG(ABS(r.our_est - a) <= 60) AS w1,
               AVG(ABS(r.our_est - a) <= 120) AS w2, AVG(r.our_est - a) AS bias,
               COUNT(r.ucop_est) AS ucop_n, AVG(ABS(r.ucop_est + 30 - a)) AS ucop_mae,
               AVG(ABS(r.ucop_est + 30 - a) <= 60) AS ucop_w1, AVG(ABS(r.ucop_est + 30 - a) <= 120) AS ucop_w2
        FROM (SELECT r.*, (o.departed_after + o.departed_by) / 2 AS a
              FROM rows r JOIN observed_departures o
                ON o.service_date = r.service_date AND o.trip_id = r.trip_id AND o.stop_code = r.stop_code
               AND (r.stop_sequence IS NULL OR o.stop_sequence = r.stop_sequence)
              WHERE r.stop_code = :stop AND r.ts >= :since AND o.departed_after IS NOT NULL
                AND o.departed_by - o.departed_after <= :unc * 2
                AND r.category NOT IN ('ucop_departed', 'ours_terminating')) r
        WHERE a - r.ts >= :lo AND a - r.ts < :hi
        """
        out = {}
        with self._lock:
            for lo, hi in buckets:
                out[f"{lo}-{hi} min"] = dict(self._db.execute(
                    q, {"stop": stop_code, "since": since, "unc": max_uncertainty_s, "lo": lo * 60, "hi": hi * 60}
                ).fetchone())
        return out

    def drift_training(self, since: int) -> list[dict]:
        """Past raw predictions (stored estimate minus the drift applied) with their observed departure,
        to warm-start the drift model after a restart."""
        with self._lock:
            return [dict(r) for r in self._db.execute(
                """SELECT r.ts, r.service_date, r.trip_id, r.stop_code, r.stop_sequence, r.our_src,
                          r.our_est - COALESCE(r.drift_s, 0) AS raw_est,
                          (o.departed_after + o.departed_by) / 2 AS actual
                   FROM rows r JOIN observed_departures o
                     ON o.service_date = r.service_date AND o.trip_id = r.trip_id AND o.stop_code = r.stop_code
                    AND o.stop_sequence = r.stop_sequence
                   WHERE r.ts >= ? AND r.our_src IN (1, 2) AND o.departed_after IS NOT NULL
                     AND o.departed_by - o.departed_after <= 180
                     AND r.category NOT IN ('ucop_departed', 'ours_terminating')""", (since,))]

    def observed_count(self, stop_code: str, since: int) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM observed_departures WHERE stop_code=? AND departed_by>=?",
                                    (stop_code, since)).fetchone()[0]

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
          {reasons},
          SUM(category IN ('match','time_diff') AND our_src=2) AS src2,
          SUM(category IN ('match','time_diff') AND our_src=1) AS src1,
          SUM(category IN ('match','time_diff') AND our_src=0) AS src0,
          SUM(headsign_differs) AS headsign_differs,
          COUNT(DISTINCT trip_id) AS trips,
          (SELECT COUNT(*) FROM snapshots WHERE stop_code=:stop AND ts>=:since) AS snapshots
        FROM rows WHERE stop_code=:stop AND ts>=:since AND category NOT IN ('ucop_departed', 'ours_terminating', 'no_ucop') {extra}
        """
        reasons = ",\n".join(
            f"SUM(category='time_diff' AND {DIFF_REASON_SQL}='{r}') AS diff_{r}" for r in DIFF_REASONS)
        q = q.replace("{reasons}", reasons)
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
