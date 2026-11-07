# Departure board server

Builds the departure list for a stop directly from GP Metro's GTFS feeds, so the board won't need UCOP's
rendered bitmap. For now it runs next to UCOP and compares the two, for the stops listed in `config.toml`.

- `GET /compare` (also `/`): one row per stop with its 24-hour agreement with UCOP.
- `GET /stops/1117/departures`: the small JSON the board will eventually fetch (~0.6 KB instead of a 48 KB BMP).
  Add `?debug=1` for every field.
- `GET /stops/1117/display.bmp`: our own 800x480 1-bit BMP, in the format the current firmware already accepts.
- `GET /compare/1117`: our image next to UCOP's, the latest trip-by-trip comparison, and a 24-hour summary.
- `GET /health`: feed ages, static feed version, and how many realtime trips match the static feed.

## How departures are worked out

For each scheduled visit to the stop, the first rule that applies:

| `src` | Meaning |
| - | - |
| 2 | The realtime feed predicts this stop. Matched on `stop_sequence`, since some trips visit the stop twice |
| 1 | The trip only has a prediction for its first stop, so the schedule is shifted by that delay |
| 0 | Schedule only |

If the trip's bus hasn't reported its position in 5 minutes, its prediction is ignored. These "ghost" buses appear
wildly late in the feed. Departures more than 60 s in the past are dropped.

GP Metro's feeds: the static zip is rebuilt nightly with a rolling ~30-day `calendar_dates.txt`. Realtime predictions
are absolute times. About a quarter of trips have no headsign, so the trip's last stop name is used instead.

## Comparing with UCOP

Every 2 minutes the server fetches UCOP's page for each stop. UCOP's image is fetched only when a comparison page is
viewed. Departures are matched on the GTFS trip ID in UCOP's `data-key` attribute and stored in `data/compare.db`
(SQLite, 14 days). Categories:

- `match`: both list the trip, with times within 90 s. UCOP shows times truncated to the minute.
- `time_diff`: both list it, but the times differ. Flags: `stale_vehicle` (we deliberately ignored a ghost bus's
  prediction) and `stale_block` (another trip on the same block has a ghost bus).
- `ours_only` / `ucop_only`: only one side lists it, within the time window both lists cover.
- `ucop_departed`: UCOP keeps departed buses listed and we don't. Not counted.
- `ours_terminating`: a trip ending at this stop that only we list. UCOP caps its lists at 40 entries and leaves many
  of these out at hubs, and our board doesn't show them. Not counted.

## Logs for debugging the GTFS handling

Everything is under `data/` on the Pi (`~/busboard/data`):

| Path | What | Kept |
| - | - | - |
| `compare.db` | Every comparison row, with debug columns: `our_src`, `stop_sequence`, `timepoint`, `vehicle_id`, `vehicle_age_s`, `rt_updates` (stop updates the trip had in the feed), `origin_delay_s`, UCOP's status text, and the `archive` directory of the inputs | 14 days |
| `archive/YYYYMMDD/HHMMSS/` | The exact inputs of each comparison: both realtime feeds and every stop's UCOP page (gzipped, ~96 KB, ~70 MB/day) | 7 days |
| `archive/static/` | Every static feed version seen | 30 versions |
| `logs/events-YYYYMMDD.jsonl` | Static rebuilds (incl. how many trip IDs survived from the previous version), feed errors and recoveries, trip-ID match dips (with a sample of unmatched IDs), UCOP errors | 14 days |

The usual loop: pull the data, find a pattern of disagreements, change the code, and **replay** the archived inputs
through the changed code to check that agreement improved before deploying:

```sh
rsync -a busbox@192.168.1.27:busboard/data/ data-pi/        # data-pi/ is gitignored
uv run python -m busboard.replay data-pi                    # per-stop summary using the current code
uv run python -m busboard.replay data-pi --stop 372 --since 20261107-0600 --mismatches
uv run python -m busboard.replay data-pi --rows rows.jsonl  # every row as JSON lines, for jq / pandas
```

Example queries on `compare.db`:

```sql
-- Disagreements by stop and our source, excluding the deliberate ghost-bus ones
SELECT stop_code, our_src, COUNT(*), AVG(diff_s) FROM rows
WHERE category = 'time_diff' AND NOT stale_vehicle GROUP BY 1, 2;
-- Predictions earlier than the schedule at timepoints (buses usually wait there)
SELECT stop_code, route, trip_id, (our_est - our_sched) AS early_s, ucop_status_text FROM rows
WHERE timepoint AND our_est < our_sched - 60 AND category IN ('match', 'time_diff');
```

## Running

```sh
cd server
uv sync
uv run pytest
uv run uvicorn busboard.app:app --port 8080     # BUSBOARD_CONFIG=... to use another config file
```

## Deploying to the Pi

```sh
server/deploy/deploy.sh            # defaults to busbox@192.168.1.27
```

This copies `server/` to `~/busboard`, installs `uv` if needed, and runs it as a systemd **user** service on port 8080.
Logs (user journals aren't stored separately on the Pi, so read them from the system journal):
`ssh busbox@192.168.1.27 journalctl _SYSTEMD_USER_UNIT=busboard.service -f`

On the Pi 3, startup takes ~25 s while the static feed is indexed.
