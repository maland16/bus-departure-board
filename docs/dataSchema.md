
### Data downstream to ESP32
- Recent & Upcoming departures (Route #, deviation from schedule, est arrival time)
- Service Alerts
- optional text? Relevant city info?

### Data upstream from ESP32
- Battery voltage (SOC estimate?)
- Solar input voltage (could do current too as a stretch)
- Temperature (battery temp, external temp?)
- Timestamp of last update

## Telemetry reference

Sent once per wake-up (Influx line protocol, see `src/departure-board/telemetry.cpp`) as the `diagnostics` measurement, tagged with `device` and `fw` (git short hash of the firmware, `-dirty` if built with uncommitted changes, `unknown` if `tools/update-git-version.sh` hasn't been run).

| Field | Meaning |
| - | - |
| `solar_v`, `battery_v` | Voltages sampled at boot, before wifi starts |
| `rssi` | Wifi signal strength (dBm) |
| `display_mode` | What's on the screen, see below |
| `display_result` | What this wake-up did to the screen, see below |
| `http_code` | Status of the image download (negative = HTTPClient error, 0 = not attempted) |
| `time_since_last_update_s` | Seconds since the live image was last successfully downloaded |
| `wifi_failures_before_connect` | Wake-ups in a row that failed to connect before this one |
| `wifi_connect_ms`, `ntp_ms`, `download_ms`, `refresh_ms` | How long each step took this wake-up (0 = skipped) |
| `awake_ms` | Time awake so far this wake-up, when the telemetry was built |
| `prev_awake_ms` | Total time awake during the previous wake-up, including sending telemetry |
| `chip_temp_c` | ESP32 internal temperature, reads a few degrees above the enclosure air |
| `wake_count` | Boots since power-on. Dropping back to a low number means power was lost |
| `crash_count`, `last_crash_reason` | Panics/watchdog resets since power-on, and the `reset_reason` value of the latest one |
| `brownout_count` | Brownout resets since power-on |
| `reset_reason` | Why this boot happened (ESP-IDF `esp_reset_reason_t`: 1 power-on, 3 software, 4 panic, 5 interrupt WDT, 6 task WDT, 7 other WDT, 8 deep sleep wake, 9 brownout) |
| `heap_free`, `heap_min` | Free heap now / lowest this boot (bytes) |

A second `network` measurement carries the connected `ssid` as a tag.

**`display_mode`**: 0 none, 1 live data, 2 night time (no longer used), 3 unavailable (low battery or stale data)

**`display_result`**: 0 not attempted (no wifi or night time), 1 refreshed, 2 unchanged (refresh skipped), 3 download failed, 4 bad image from server
