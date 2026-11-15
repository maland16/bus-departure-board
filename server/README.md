# Departure board server

Builds each stop's list of upcoming departures from GP Metro's GTFS feeds and publishes it as a small JSON file
for the e-ink boards. It also watches vehicle positions to see when buses actually leave each stop, and logs its
predictions next to those departures, so the estimates can keep being improved.

- `GET /`: status dashboard: is publishing working, are the feeds fresh, is logging keeping up, per-stop departures,
  accuracy and drift correction, and recent events.
- `GET /stops/1117/departures`: the payload as last published (the boards fetch the published copy, see below).
  Add `?debug=1` for every computed field.
- `GET /health`: the same status as JSON (503 if the schedule isn't loaded or TripUpdates are over 5 min old).

Every 30 s the server fetches the realtime feeds, computes every stop's departures once, and pushes the payloads to
the web host. Every 2 min it also logs the predictions and archives the raw feeds.

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
- Estimates allow for buses waiting at timepoints the bus hasn't reached yet, which the feed doesn't model (it can show
  a bus as 12 min early for a whole trip). A trip that hasn't started is never shown early, and a trip with no bus
  assigned yet has its lateness capped at 10 minutes, since that lateness is inherited through the block and can run away.
- **Drift correction** (`drift_correction`, on): buses leave a steady ~1 min later than GP Metro predicts, by an amount
  that varies by route and place but not with how far ahead the prediction was made. For each route, direction and
  stop, the server learns that offset from actual departures (from vehicle positions) over the last 3 hours, and
  shifts predictions by it, scaled down until a few departures have been seen, capped at 5 minutes and faded out in the
  last 3 minutes before a departure, where the feed is already good. Feed glitches (errors over 10 min) and vaguely
  known departures aren't learned from. It never moves a departure before its scheduled time at a timepoint. Learned
  state is rebuilt from `busboard.db` on restart; the dashboard and `/health` show the corrections.
- Optional, off by default (`block_propagation`): a trip can't start before its bus finishes the previous trip on the
  same block. Re-measure with `busboard.accuracy` before turning it on.
- Departures more than 60 s in the past are dropped.

GP Metro's feeds: the static zip is rebuilt nightly with a rolling ~30-day `calendar_dates.txt`. Realtime predictions
are absolute times. About a quarter of trips have no headsign, so the trip's last stop name is used instead.

## Accuracy against actual departures

Each realtime poll (every 30 s), the server tracks every bus on a trip that serves a watched stop. GP Metro's vehicles
report the stop they're at or heading to, so a stop is departed once the bus reports a later one. Each departure goes
into `observed_departures` in `busboard.db`, with the window it happened in (`departed_after`, `departed_by`). The
dashboard shows each stop's mean absolute error for predictions made 5-20 minutes ahead over the last 24 hours.

To test a change to the estimates against real outcomes, replay the archive through it:

```sh
rsync -a busbox@192.168.1.27:busboard/data/ data-pi/        # data-pi/ is gitignored
uv run python -m busboard.accuracy data-pi                     # by minutes before departure
uv run python -m busboard.accuracy data-pi --by src            # or --by stop / route
uv run python -m busboard.accuracy data-pi --config trial.toml # e.g. with block_propagation = true
uv run python -m busboard.accuracy data-pi --rows acc.jsonl    # every scored prediction
```

Actual departures come from the live observations where available (30 s resolution). Otherwise they're worked out
from the archived vehicle positions (2 min resolution). Stops added to the config later can be scored on older
archives too, since predictions only need the realtime feeds.

## Logs

Everything is under `data/` on the Pi (`~/busboard/data`):

| Path | What | Kept |
| - | - | - |
| `busboard.db` `predictions` | Every 2 min, each stop's departures up to 60 min ahead, as served (`est` includes the drift correction, `drift_s` says how much), with `shown` (in the payload) and debug columns: `src`, `stop_sequence`, `vehicle_id`, `vehicle_age_s`, `rt_updates`, `origin_delay_s`, `ref_stop_sequence`, `timepoint_wait_s`, `held_at_timepoint`, `from_vehicle`, `stale_vehicle` | 30 days |
| `busboard.db` `observed_departures` | When buses actually left each watched stop | 30 days |
| `archive/YYYYMMDD/HHMMSS/` | Both realtime feeds every 2 min (gzipped, ~15 MB/day), for replaying | 30 days |
| `archive/static/` | Every static feed version seen | 60 versions |
| `logs/events-YYYYMMDD.jsonl` | Startup, static rebuilds (incl. how many trip IDs survived), feed and publish errors and recoveries, trip-ID match dips (with a sample of unmatched IDs) | 30 days |

`busboard.db` was `compare.db` until the UCOP comparison was removed (2026-11). On first start the server renames it,
keeps our side of the comparison rows as `predictions` and drops the rest.

Live accuracy, straight from the database (no replay needed):

```sql
-- Mean absolute error by minutes ahead, last 7 days
SELECT (a - p.ts) / 300 * 5 AS min_ahead, COUNT(*), AVG(ABS(p.est - a)) / 60.0 AS mae_min
FROM predictions p JOIN (SELECT *, (departed_after + departed_by) / 2 AS a FROM observed_departures
                         WHERE departed_after IS NOT NULL AND departed_by - departed_after <= 180) o
  USING (service_date, trip_id, stop_code, stop_sequence)
WHERE p.ts > strftime('%s', 'now') - 7 * 86400 AND a > p.ts GROUP BY 1 ORDER BY 1;
```

## Publishing for the boards

The boards fetch a small JSON file per stop from a static web host. After every realtime poll (30 s)
the Pi writes the files into `data/publish/v1/` and pushes them with rsync over SSH. The connection is outbound only,
so nothing is opened on the home network. If the Pi or the home internet goes down, the files stop updating; the
boards will see an old `now`: they keep counting down from the last departures they have, and show a "Live tracking unavailable" screen once those are 30 minutes old.

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

- The dashboard (and `/health`'s `publish` section) shows the last successful push, its duration, and how old the
  data was when the host served it back (`served_lag_s`, checked every 10 min).
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
