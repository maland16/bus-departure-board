#define ENABLE_GxEPD2_GFX 0

#include <ESP32Time.h>

#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiGeneric.h>
#include <WiFiMulti.h>
#include <WiFiClientSecure.h>

#include "credentials.h"
#include "debug-print.h"
#include "display-driver.h"
#include "wifi-driver.h"
#include "rtc-driver.h"

#define DEBUG_MODE

#define SERIAL_BAUD (115200)

#define RTC_UPDATE_PERIOD_MIN (60)
#define RTC_UPDATE_PERIOD_SEC (RTC_UPDATE_PERIOD_MIN * 60)
#define RTC_UPDATE_PERIOD_MS (RTC_UPDATE_PERIOD_SEC * 1000)

#define MAIN_TASK_PERIOD_MS (1000)
#define DISPLAY_UPDATE_PERIOD_SEC (60)
#define DISPLAY_UPDATE_PERIOD_MS (DISPLAY_UPDATE_PERIOD_SEC * 1000)

// When to stop/start fetching live data
#define WAKE_UP_TIME_HOUR (5) // 5AM
#define SLEEP_TIME_HOUR (11 + 12) // 11PM

enum MainState {
    STATE_DEFAULT = 0,
    STATE_RUNNING,
    STATE_DISCONNECTED,
    STATE_SLEEPING,
    STATE_LOW_POWER,
};

// FreeRTOS tasks
TaskHandle_t updateRTCTaskHandle = NULL;
TaskHandle_t mainDisplayTaskHandle = NULL;

MainState mainState = STATE_DEFAULT;
unsigned long lastDisplayUpdateMs = 0;

void setup() {
  // put your setup code here, to run once:
  Serial.begin(SERIAL_BAUD);
  DEBUG_PRINTLN("Serial Initialized");
  initDisplay();
  DEBUG_PRINTLN("Display Initialized");

  initWifi();

  DEBUG_PRINTLN("Waiting for NTP time sync");
  initClock();
  DEBUG_PRINTLN("Clock Initialized");

  initWifiClient();

  connectAndGetImage();

  Serial.println("Starting updateRTCTask()");
  xTaskCreatePinnedToCore(
    updateRTCTask,         // Task function
    "updateRTCTask",       // Task name
    10000,             // Stack size (bytes)
    NULL,              // Parameters
    1,                 // Priority
    &updateRTCTaskHandle,  // Task handle
    0                  // Core 0
  );

  Serial.println("Starting mainDisplayTask()");
  xTaskCreatePinnedToCore(
    mainDisplayTask,         // Task function
    "mainDisplayTask",       // Task name
    10000,             // Stack size (bytes)
    NULL,              // Parameters
    1,                 // Priority
    &mainDisplayTaskHandle,  // Task handle
    1                  // Core 1
  );
  
  delay(5000);

  /*
  for(int i = 0; i < 5; i++) {
    addDateTimeToPageBuffer();
    delay(2000);
  }
  */
  
  clearScreenPowerOff();
}

void updateRTCTask(void *parameter) {
  for (;;) { // Infinite loop
    vTaskDelay(RTC_UPDATE_PERIOD_MS / portTICK_PERIOD_MS);
    DEBUG_PRINTLN("Updated RTC from Internet");
    updateRTCFromNPT();
  }
}

void mainDisplayTask(void *paremeter) {
  for (;;) { // Infinite loop
    vTaskDelay(MAIN_TASK_PERIOD_MS / portTICK_PERIOD_MS);
    
    mainStateMachine();
  }
}

void mainStateMachine(void) {
  // Check for enter low power
  // Check for enter sleep

  switch(mainState) {
    case STATE_DEFAULT: {
      // Do nothing, init will take us out of this
      break;
    }
    case STATE_RUNNING: {
      // Update display periodically
      if(lastDisplayUpdateMs - millis() > DISPLAY_UPDATE_PERIOD_MS) {
        // Time to update the display!
        connectAndGetImage();
        lastDisplayUpdateMs = millis();
      }

      break;
    }
    case STATE_DISCONNECTED: {
      // Attempt to re-connect to wifi
      break;
    }
    case STATE_SLEEPING: {
      // night time zzzzzz
      break;
    }
    case STATE_LOW_POWER: {
      // Low SOC
      break;
    }
  }
}

void loop() {
  // put your main code here, to run repeatedly:

}