#define ENABLE_GxEPD2_GFX 0
#define DEBUG_MODE
#define configCHECK_FOR_STACK_OVERFLOW 2

#include <ESP32Time.h>
#include <esp_task_wdt.h>

#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiGeneric.h>
#include <WiFiMulti.h>
#include <WiFiClientSecure.h>

#include "battery-driver.h"
#include "credentials.h"
#include "debug-print.h"
#include "deep-sleep-driver.h"
#include "display-driver.h"
#include "rtc-driver.h"
#include "task-supervisor.h"
#include "wifi-driver.h"
#include "telemetry.h"

#define SERIAL_BAUD (115200)

// When to stop/start fetching live data
#define WAKE_UP_TIME_HOUR (4) // 4AM
#define SLEEP_TIME_HOUR (12 + 11) // 11PM

#define STOP_BASE_URL "https://transit.ucop.me/stops/"

// How long should we wait before considering the data on the display to be too stale
#define DISPLAY_STALE_SECONDS (30 * 60)

RTC_DATA_ATTR DisplayMode lastDisplayedMode = DISPLAY_MODE_NONE;
RTC_DATA_ATTR uint32_t lastDisplayUpdateEpoch = 0;

static const char *stopURL = STOP_BASE_URL CONFIGURED_STOP_ID "/";
static const char *stopBMPURL = STOP_BASE_URL CONFIGURED_STOP_ID "/display/gdem075t41wt/display.bmp";

bool isNightTime(void) {
  const int currentMinuteOfDay = (rtc.getHour(true) * 60) + rtc.getMinute();
  const int sleepMinuteOfDay = (SLEEP_TIME_HOUR * 60);
  const int wakeMinuteOfDay = (WAKE_UP_TIME_HOUR * 60);

  return currentMinuteOfDay >= sleepMinuteOfDay || currentMinuteOfDay < wakeMinuteOfDay;
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
  delay(1000); // Give time for Serial to initialize, and serial client to innumerate the port and connect
  DEBUG_PRINTLN("Serial Initialized");
  Serial.println("--- DEPARTURE BOARD STARTUP ---");

  initClock();

  esp_reset_reason_t r = esp_reset_reason();
  printResetReason(r);

  initDisplayDriver();

  // If battery voltage is too low, go to sleep and hope for sun
  initBatteryDriver();
  printVoltages();
  if (isBatteryBelowCutoffVoltage()) {
    lowBatteryHelper();
  }

  initWifi();

  DEBUG_PRINTLN("Waiting for NTP time sync");
  updateRTCFromNPT();

  // If we have a good RTC time and it's night time, display nighttime image
  if (getRTCValid() && isNightTime()) {
    nightTimeDisplayHelper();
    // this^ call puts us to sleep for the night, so nothing below is reached
  }

  // Battery is good, and it's day time, display the upcoming busses!
  updateDisplay();

  sendTelemetryWithRetries();

  deepSleepForMinutes(1);
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

    showUnavailableImage(stopURL, "Low battery");
    lastDisplayedMode = DISPLAY_MODE_UNAVAILABLE;
  } else {
    Serial.println("Low battery display already active");
  }

  deepSleepForMinutes(30);
}

void nightTimeDisplayHelper(void) {

  if (lastDisplayedMode != DISPLAY_MODE_NIGHTTIME) {
    Serial.println("Nighttime display not active; displaying nighttime image.");
    lastDisplayedMode = DISPLAY_MODE_NIGHTTIME;
  } else {
    Serial.println("Nighttime display already active");
  }

  Serial.println("It's night time! Sleeping until the wake-up hour.");

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
  const uint32_t timeSinceLastDisplayUpdate = getTimeSinceLastUpdate();

  esp_task_wdt_add(NULL); // Start & 
  esp_task_wdt_reset();   // feed watchdog in case things get weird
  bool displayedImageSuccessfully = drawBmpFromUrl(stopBMPURL);
  esp_task_wdt_delete(NULL);

  if (displayedImageSuccessfully) {
    Serial.println("Image displayed successfully");
    lastDisplayedMode = DISPLAY_MODE_LIVE_DATA;
    lastDisplayUpdateEpoch = rtc.getEpoch();
  }

  if (lastDisplayUpdateEpoch != 0 && timeSinceLastDisplayUpdate > DISPLAY_STALE_SECONDS) {
    Serial.printf("Last successful display update was %lu seconds ago; showing unavailable image.\n",
                  (unsigned long)timeSinceLastDisplayUpdate);

    // Only update the display if we haven't already displayed the unavailable image
    if(lastDisplayedMode != DISPLAY_MODE_UNAVAILABLE) {
      showUnavailableImage(stopURL, "Data stale");
      lastDisplayedMode = DISPLAY_MODE_UNAVAILABLE;
    }
  }
}

void loop() { }