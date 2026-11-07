# Departure board server

Builds the departure list for a stop directly from GP Metro's GTFS feeds, so the board won't need UCOP's
rendered bitmap. For now it runs next to UCOP and compares the two, for the stops listed in `config.toml`. It also
watches vehicle positions to see when buses actually leave each stop, so predictions (ours and UCOP's) can be scored
against reality. That works for any stop, whether or not UCOP covers it.

- `GET /compare` (also `/`): one row per stop with its 24-hour agreement with UCOP and accuracy against actual departures.
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
| 1 | No prediction for this stop: the schedule is shifted by the delay at the nearest stop that has one, preferring an earlier stop (usually the first) |
| 0 | Schedule only |

- If the trip's bus hasn't reported its position in 15 minutes, its prediction is ignored. These "ghost" buses appear
  wildly late in the feed.
- At timepoints, a departure is never shown earlier than scheduled: buses wait there, even when the feed says
  otherwise. This doesn't apply where a trip ends.
- Trips that drop out of the feed for a poll keep their last prediction for 2 minutes.
- If a trip has no usable TripUpdate but its bus is reporting its position, the delay is taken from where the bus is
  (its report time minus its scheduled time at the stop it's at or heading to). If the bus is already past this
  stop, the departure is dropped.
- **Drift correction** (`drift_correction`, on): GP Metro's predictions are right just before a bus leaves but drift
  with lead time, by route and place along it: on 2026-11-07 buses at Congress St + Park/State left ~3 min later
  than predicted 20+ min ahead, while other routes left early. For each route, direction and stop, the server
  fits how far actual departures (from vehicle positions) drifted per minute of lead over the last 3 hours, and
  shifts predictions by that, scaled down until a few departures have been seen and capped at 5 minutes. It never
  moves a departure before its scheduled time at a timepoint. Learned state is rebuilt from `compare.db` on restart;
  `/health` shows the largest corrections.
- Optional, off by default (`block_propagation`): a trip can't start before its bus finishes the previous trip on the
  same block. Re-measure with `busboard.accuracy` before turning it on.
- Departures more than 60 s in the past are dropped.

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
- `drift_correction` (a `diff_reason`): our drift correction moved the time, and the uncorrected prediction matched
  UCOP. Expected, since UCOP doesn't correct.
- `no_ucop`: UCOP's page couldn't be fetched. Our departures are still logged for accuracy scoring. Not counted.

Each `time_diff` also gets a `diff_reason`. `stale_vehicle`, `stale_block`, `held_at_timepoint` and
`gone_from_feed` are cases where UCOP shows something stale or impossible. Anything left over (`src2`, `src1`,
`src0`) is worth investigating.

UCOP has stop pages but no display image for 586, 585, 148 and 147. The comparison works the same; the stop page
just shows a note in place of UCOP's image.

## Accuracy against actual departures

Each realtime poll (every 30 s), the server tracks every bus on a trip that serves a watched stop. GP Metro's vehicles
report the stop they're at or heading to, so a stop is departed once the bus reports a later one. Each departure goes
into `observed_departures` in `compare.db`, with the window it happened in (`departed_after`, `departed_by`). The
comparison pages score predictions made 0-5, 5-20 and 20-60 minutes before each departure.

To test a change to the GTFS handling against real outcomes, replay the archive through it:

```sh
uv run python -m busboard.accuracy data-pi                     # by minutes before departure, ours vs UCOP
uv run python -m busboard.accuracy data-pi --by src            # or --by stop / route
uv run python -m busboard.accuracy data-pi --config trial.toml # e.g. with block_propagation = true
uv run python -m busboard.accuracy data-pi --rows acc.jsonl    # every scored prediction
```

Actual departures come from the live observations where available (30 s resolution). Otherwise they're worked out
from the archived vehicle positions (2 min resolution). Stops added to the config later can be scored on older
archives too, since our predictions only need the realtime feeds.

## Logs for debugging the GTFS handling

Everything is under `data/` on the Pi (`~/busboard/data`):

| Path | What | Kept |
| - | - | - |
| `compare.db` `observed_departures` | When buses actually left each watched stop | 14 days |
| `compare.db` `rows` | Every comparison row, with debug columns: `our_src`, `stop_sequence`, `timepoint`, `vehicle_id`, `vehicle_age_s`, `rt_updates` (stop updates the trip had in the feed), `origin_delay_s`, UCOP's status text, and the `archive` directory of the inputs | 14 days |
| `archive/YYYYMMDD/HHMMSS/` | The exact inputs of each comparison: both realtime feeds and every stop's UCOP page (gzipped, ~136 KB for 14 stops, ~100 MB/day) | 7 days |
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

## Publishing for the boards

The boards will fetch a small JSON file per stop from a static web host, instead of UCOP's 48 KB bitmap. Every 30 s
the Pi writes the files into `data/publish/v1/` and pushes them with rsync over SSH. The connection is outbound only,
so nothing is opened on the home network. If the Pi or the home internet goes down, the files stop updating; the
boards will see an old `now` and fall back to UCOP.

```
https://board.devinmalanaphy.com/v1/index.json          {"v":1,"now":...,"stops":{"1117":"Congress St + Center St",...}}
https://board.devinmalanaphy.com/v1/stops/1117.json     one payload per stop
```

### Payload (schema v1)

```json
{"v":1,"now":1794070000,"tz":-18000,"live":true,"stop":"1117","name":"Congress St + Center St",
 "deps":[{"r":"4","h":"Metro Pulse","t":1794070420,"s":1794070380,"src":2}, ...],"alerts":[]}
```

| Field | Meaning |
| - | - |
| `now` | When it was generated (epoch s). Treat old payloads as stale |
| `tz` | The stop's UTC offset at `now` in seconds (-18000 = EST), for showing clock times without DST rules on the board |
| `live` | `false` when GP Metro's realtime feed has been down for 10 min: every time is the schedule |
| `deps` | Up to 12 departures, soonest first, excluding trips that end here. 12 rather than 8 so a board that fetches every few minutes still has rows left as buses leave |
| `r`, `h` | Route and headsign |
| `t`, `s` | Estimated and scheduled departure (epoch s) |
| `src` | 2 = predicted for this stop, 1 = estimated from another stop, 0 = schedule only |

New fields may be added within v1; boards should ignore ones they don't know.

### DreamHost setup (one time)

Panel labels may differ slightly.

1. **Users:** add a user of type **SFTP/SSH (shell)**, e.g. `busboard_pub`. A dedicated user means the Pi's key can't
   touch the main website.
2. **Websites:** add `board.devinmalanaphy.com`, fully hosted, owned by `busboard_pub`. Its directory is
   `/home/busboard_pub/board.devinmalanaphy.com`. DNS is created automatically, since the domain uses DreamHost's
   nameservers.
3. **Secure Certificates:** add the free Let's Encrypt certificate for the subdomain.
4. Note the SSH host the panel shows for the user.
5. On the Pi, install the upload key. You'll be asked for the user's password once:
   `ssh -t busbox@192.168.1.27 ssh-copy-id -i ~/.ssh/busboard_publish.pub busboard_pub@<ssh host>`
6. In `config.toml` (your local copy of `config.example.toml`), set `[publish] enabled = true` and
   `target = "busboard_pub@<ssh host>:board.devinmalanaphy.com/v1"`, then deploy.

The publisher uploads a `.htaccess` (from `deploy/dreamhost/htaccess`) into `v1/`. It turns off caching (the main site
sends `max-age=600`), sets the JSON content type and disables directory listing. rsync never deletes anything on the
host, and the target must end in `/v1`.

### Checking it

- `/health` has a `publish` section: last successful push, its duration, and how old the data was when the host
  served it back (`served_lag_s`, checked every 10 min).
- Events: `publish_error` / `publish_recovered` when pushes start or stop failing, `publish_served_stale` if the host
  serves data over 2 min old (caching, or pushes landing in the wrong directory).
- `curl -sI https://board.devinmalanaphy.com/v1/stops/1117.json` should show `content-type: application/json` and
  `cache-control: no-cache, max-age=0`.
- If pushes fail with "Permission denied (publickey)", repeat step 5. Test with
  `ssh -i ~/.ssh/busboard_publish -o BatchMode=yes busboard_pub@<ssh host> true` on the Pi.

## Running

```sh
cd server
cp config.example.toml config.toml   # first time only: config.toml holds this machine's settings and isn't in git
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
