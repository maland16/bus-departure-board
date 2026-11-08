#define ENABLE_GxEPD2_GFX 0

#include <ESP32Time.h>
#include <esp_task_wdt.h>
#if ARDUINO_USB_MODE == 0
#include <USB.h>
#endif

#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiGeneric.h>
#include <WiFiMulti.h>
#include <WiFiClientSecure.h>

#include "battery-driver.h"
#include "credentials.h"
#include "deep-sleep-driver.h"
#include "display-driver.h"
#include "rtc-driver.h"
#include "task-supervisor.h"
#include "wifi-driver.h"
#include "telemetry.h"
#include "wake-stats.h"

#define SERIAL_BAUD (115200)

// USB re-enumerates on every wake-up, so give the host a moment to mount it
// before deciding we're on battery. Both are measured from boot (millis()).
// Tune USB_MOUNT_GRACE_MS from the "USB mounted after" log line.
#define USB_MOUNT_GRACE_MS (300)
#define SERIAL_MONITOR_WAIT_MS (1500)

// When to stop/start fetching live data
#define WAKE_UP_TIME_HOUR (4) // 4AM
#define SLEEP_TIME_HOUR (12 + 11) // 11PM

// Departures for the stop, published by the board server (server/ in this repo)
#define BOARD_DATA_URL "https://board.devinmalanaphy.com/v1/stops/" CONFIGURED_STOP_ID ".json"
// The "unavailable" screen's QR code links here (UCOP has a page for every stop)
#define STOP_PAGE_URL "https://transit.ucop.me/stops/" CONFIGURED_STOP_ID "/"

// How old the departures (from when the server made them) can get before we
// stop showing them. Until then a failed download just redraws the last ones.
#define DISPLAY_STALE_SECONDS (30 * 60)

// Back off how often we wake up while wifi is down, to save battery.
// The first few failures retry quickly in case it was just a blip.
#define WIFI_FAILURES_BEFORE_SHORT_BACKOFF (3)
#define WIFI_FAILURES_BEFORE_LONG_BACKOFF (6)
#define REFRESH_INTERVAL_MINUTES (1)
#define SHORT_BACKOFF_MINUTES (5)
#define LONG_BACKOFF_MINUTES (15)

RTC_DATA_ATTR DisplayMode lastDisplayedMode = DISPLAY_MODE_NONE;
// When the server generated the departures we last downloaded (epoch s)
RTC_DATA_ATTR uint32_t lastDisplayUpdateEpoch = 0;

bool isNightTime(void) {
  const int currentMinuteOfDay = (rtc.getHour(true) * 60) + rtc.getMinute();
  const int sleepMinuteOfDay = (SLEEP_TIME_HOUR * 60);
  const int wakeMinuteOfDay = (WAKE_UP_TIME_HOUR * 60);

  return currentMinuteOfDay >= sleepMinuteOfDay || currentMinuteOfDay < wakeMinuteOfDay;
}

/**
 * Wait for a serial monitor to connect, but only if we're plugged into a
 * computer. In the field USB never mounts, so this costs USB_MOUNT_GRACE_MS
 * instead of a fixed delay on every wake-up.
 */
void waitForSerialMonitor(void) {
#if ARDUINO_USB_MODE == 0 // USB-OTG (TinyUSB), the Feather's default
  while (!USB && millis() < USB_MOUNT_GRACE_MS) {
    delay(5);
  }
  if (!USB) {
    return; // not plugged into a computer
  }
  const uint32_t usbMountedMs = millis();

  while (!Serial && millis() < SERIAL_MONITOR_WAIT_MS) {
    delay(5);
  }
  Serial.printf("USB mounted after %lu ms, serial monitor %s after %lu ms\n",
                (unsigned long)usbMountedMs, Serial ? "connected" : "not connected",
                (unsigned long)millis());
#else
  delay(1000); // Hardware CDC mode, just give the serial client time to connect
#endif
}

uint32_t getRefreshIntervalMinutes(void) {
  if (consecutiveWifiFailures >= WIFI_FAILURES_BEFORE_LONG_BACKOFF) {
    return LONG_BACKOFF_MINUTES;
  }
  if (consecutiveWifiFailures >= WIFI_FAILURES_BEFORE_SHORT_BACKOFF) {
    return SHORT_BACKOFF_MINUTES;
  }
  return REFRESH_INTERVAL_MINUTES;
}

void setup() {
  
  esp_task_wdt_config_t twdt_config = {
    .timeout_ms = 30000,    // Time in milliseconds (20 seconds)
    .idle_core_mask = 0, 
    .trigger_panic = true   // Enable panic/reset on timeout
  };

  esp_task_wdt_reconfigure(&twdt_config);

  // put your setup code here, to run once:
  Serial.begin(SERIAL_BAUD);
  waitForSerialMonitor();
  Serial.println("--- DEPARTURE BOARD STARTUP ---");

  initClock();

  esp_reset_reason_t r = esp_reset_reason();
  printResetReason(r);
  recordResetReason(r);

  initDisplayDriver();

  // If battery voltage is too low, go to sleep and hope for sun
  // Must happen before initWifi(), see initBatteryDriver()
  initBatteryDriver();
  printVoltages();
  if (isBatteryBelowCutoffVoltage()) {
    lowBatteryHelper();
  }

  initWifi();

  // The clock is set from the departures download (updateDisplay()), so on a
  // wake-up this is the time kept by the RTC through deep sleep.
  // If we have a good RTC time and it's night time, sleep until morning.
  if (getRTCValid() && isNightTime()) {
    nightTimeSleepHelper();
    // this^ call puts us to sleep for the night, so nothing below is reached
  }

  // Battery is good, and it's day time, display the upcoming busses!
  updateDisplay();

  sendTelemetryWithRetries();

  deepSleepForMinutes(getRefreshIntervalMinutes());
}

/**
 * Measured battery level is too low, we need to make sure the low battery
 * image is displayed, and sleep for a while in hopes the sun comes out
 */
void lowBatteryHelper(void) {
  Serial.println("Battery is below cutoff voltage! Sleeping in hopes of more solar");

  // If low batt image isn't displayed (check NVMEM), display it and set the bit in NVMEM
  if (lastDisplayedMode != DISPLAY_MODE_UNAVAILABLE) {
    Serial.println("Unavailable display not active; displaying unavailable image.");

    showUnavailableImage(STOP_PAGE_URL, "Low battery");
    lastDisplayedMode = DISPLAY_MODE_UNAVAILABLE;
  } else {
    Serial.println("Low battery display already active");
  }

  deepSleepForMinutes(30);
}

void nightTimeSleepHelper(void) {
  Serial.println("It's night time! Sleeping until the wake-up hour.");

  // Leave the last departures up overnight, but as clock times: a countdown
  // would sit there being wrong until morning
  if (lastDisplayedMode == DISPLAY_MODE_LIVE_DATA && haveBoardData()) {
    drawBoard(true);
  }

  sendTelemetryWithRetries();

  const int currentMinuteOfDay = (rtc.getHour(true) * 60) + rtc.getMinute();
  const int wakeMinuteOfDay = (WAKE_UP_TIME_HOUR * 60);

  int minutesUntilWake = 0;

  if (currentMinuteOfDay >= wakeMinuteOfDay) {
    minutesUntilWake = (24 * 60 - currentMinuteOfDay) + wakeMinuteOfDay;
  } else {
    minutesUntilWake = wakeMinuteOfDay - currentMinuteOfDay;
  }

  if (minutesUntilWake <= 0) {
    minutesUntilWake = 1;
  }

  Serial.printf("Sleeping for %d minute(s) until %02d:00\n", minutesUntilWake, WAKE_UP_TIME_HOUR);
  deepSleepForMinutes((uint32_t)minutesUntilWake);
}

void updateDisplay(void) {
  if (isWifiConnected()) {
    esp_task_wdt_add(NULL); // Start &
    esp_task_wdt_reset();   // feed watchdog in case things get weird
    if (fetchBoardData(BOARD_DATA_URL)) {
      lastDisplayUpdateEpoch = boardDataEpoch();
    }
    esp_task_wdt_delete(NULL);
  } else {
    Serial.println("No wifi; skipping download");
  }

  if (!haveBoardData()) {
    return; // nothing downloaded since power-on, leave the screen alone
  }

  const uint32_t dataAge = getTimeSinceLastUpdate();
  if (dataAge > DISPLAY_STALE_SECONDS) {
    Serial.printf("Departures are %lu seconds old; showing unavailable image.\n", (unsigned long)dataAge);

    // Only update the display if we haven't already displayed the unavailable image
    if (lastDisplayedMode != DISPLAY_MODE_UNAVAILABLE) {
      showUnavailableImage(STOP_PAGE_URL, "Data stale");
      lastDisplayedMode = DISPLAY_MODE_UNAVAILABLE;
    }
    return;
  }

  // Fresh or cached departures: count down from the current time
  drawBoard(false);
  lastDisplayedMode = DISPLAY_MODE_LIVE_DATA;
}

void loop() { }