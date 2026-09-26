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

#define SERIAL_BAUD (115200)

// When to stop/start fetching live data
#define WAKE_UP_TIME_HOUR (5) // 5AM
#define SLEEP_TIME_HOUR (11 + 12) // 11PM

static const char *ucopURL = "https://transit.ucop.me/stops/1117/display/gdem075t41wt/display.bmp";

void setup() {
  
  esp_task_wdt_config_t twdt_config = {
    .timeout_ms = 30000,                            // Time in milliseconds (20 seconds)
    .idle_core_mask = 0, 
    .trigger_panic = true                           // Enable panic/reset on timeout
  };

  // 2. Initialize the watchdog using the struct pointer
  esp_task_wdt_reconfigure(&twdt_config);

  // put your setup code here, to run once:
  Serial.begin(SERIAL_BAUD);
  delay(1000); // Give time for Serial to initialize
  DEBUG_PRINTLN("Serial Initialized");
  
  esp_reset_reason_t r = esp_reset_reason();
  printResetReason(r); // Print reset reason to console

  initDeepSleepDriver();
  initBatteryDriver();

  Serial.printf("Battery Voltage: %.2f V\n", readBatteryVoltage());

  if (isBatteryBelowCutoffVoltage()) {
    lowBatteryHandler();
  }

  initDisplayDriver();
  DEBUG_PRINTLN("Display Initialized");

  initWifi();

  DEBUG_PRINTLN("Waiting for NTP time sync");
  initClock();
  DEBUG_PRINTLN("Clock Initialized");
  Serial.printf("Current time: %04d-%02d-%02d %02d:%02d:%02d\n",
                rtc.getYear(), rtc.getMonth(), rtc.getDay(),
                rtc.getHour(true), rtc.getMinute(), rtc.getSecond());

  updateDisplayAndSleep();
}


/**
 * Measured battery level is too low, we need to make sure the low battery
 * image is displayed, and sleep for a while in hopes the sun comes out
 */
void lowBatteryHandler(void) {
  // If low batt image isn't displayed (check NVMEM), display it and set the bit in NVMEM

  Serial.println("Battery is below cutoff voltage! Sleeping in hopes of more solar");
}

void updateDisplayAndSleep(void) {
  esp_task_wdt_add(NULL); // Start & 
  esp_task_wdt_reset();   // feed watchdog in case things get weird
  drawBmpFromUrl(ucopURL);
  esp_task_wdt_delete(NULL);

  deepSleepForMinutes(1);
}

void loop() {
  // put your main code here, to run repeatedly:

}