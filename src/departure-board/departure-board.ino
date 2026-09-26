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

#include "credentials.h"
#include "debug-print.h"
#include "display-driver.h"
#include "rtc-driver.h"
#include "task-supervisor.h"
#include "wifi-driver.h"

#define SERIAL_BAUD (115200)

#define RTC_UPDATE_PERIOD_MIN (60UL)
#define RTC_UPDATE_PERIOD_SEC (RTC_UPDATE_PERIOD_MIN * 60UL)
#define RTC_UPDATE_PERIOD_MS (RTC_UPDATE_PERIOD_SEC * 1000UL)

#define MAIN_TASK_PERIOD_MS (1000UL)
#define DISPLAY_UPDATE_PERIOD_SEC (10UL)
#define DISPLAY_UPDATE_PERIOD_MS (DISPLAY_UPDATE_PERIOD_SEC * 1000UL)
#define HEARTBEAT_TASK_PERIOD_MS (10000UL)
#define HEARTBEAT_LED_PIN LED_BUILTIN

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

static const char *ucopURL = "https://transit.ucop.me/stops/1117/display/gdem075t41wt/display.bmp";

// FreeRTOS tasks
TaskHandle_t updateRTCTaskHandle = NULL;
TaskHandle_t mainDisplayTaskHandle = NULL;
TaskHandle_t heartbeatTaskHandle = NULL;
TaskHandle_t supervisorTaskHandle = NULL;

MainState mainState = STATE_DEFAULT;
unsigned long lastDisplayUpdateMs = 0U;

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
  DEBUG_PRINTLN("Serial Initialized");

  esp_reset_reason_t r = esp_reset_reason();
  Serial.printf("Reset reason: %d\n", (int)r);

  pinMode(HEARTBEAT_LED_PIN, OUTPUT);
  digitalWrite(HEARTBEAT_LED_PIN, LOW);

  initDisplayDriver();
  DEBUG_PRINTLN("Display Initialized");

  initWifi();

  DEBUG_PRINTLN("Waiting for NTP time sync");
  initClock();
  DEBUG_PRINTLN("Clock Initialized");



  /*
  xTaskCreatePinnedToCore(
    vSupervisorTask,         // Task function
    "vSupervisorTask",       // Task name
    5000,             // Stack size (bytes)
    NULL,              // Parameters
    1,                 // Priority
    &supervisorTaskHandle,  // Task handle
    0                  // Core 0
  );

  registerTaskWithSupervisor(supervisorTaskHandle, "vSupervisorTask", 5000);
  
  Serial.println("Starting updateRTCTask()");
  xTaskCreatePinnedToCore(
    updateRTCTask,         // Task function
    "updateRTCTask",       // Task name
    5000,             // Stack size (bytes)
    NULL,              // Parameters
    1,                 // Priority
    &updateRTCTaskHandle,  // Task handle
    0                  // Core 0
  );

  registerTaskWithSupervisor(updateRTCTaskHandle, "updateRTCTask", 5000);
*/
  Serial.println("Starting heartbeatTask()");
  xTaskCreatePinnedToCore(
    heartbeatTask,
    "heartbeatTask",
    4096,
    NULL,
    1,
    &heartbeatTaskHandle,
    1
  );

  Serial.println("Starting mainDisplayTask()");
  xTaskCreatePinnedToCore(
    mainDisplayTask,         // Task function
    "mainDisplayTask",       // Task name
    20000,             // Stack size (bytes)
    NULL,              // Parameters
    1,                 // Priority
    &mainDisplayTaskHandle,  // Task handle
    1                  // Core 1
  );

  // registerTaskWithSupervisor(mainDisplayTaskHandle, "mainDisplayTask", 10000);
  
  mainState = STATE_RUNNING; // Hard code to running for now

  // delay(60000);

  /*
  for(int i = 0; i < 5; i++) {
    addDateTimeToPageBuffer();
    delay(2000);
  }
  */
  
  // clearScreenPowerOff();
}

void updateRTCTask(void *parameter) {
  for (;;) { // Infinite loop
    vTaskDelay(RTC_UPDATE_PERIOD_MS / portTICK_PERIOD_MS);
    updateRTCFromNPT();
    DEBUG_PRINTLN("Updated RTC from Internet");
  }
}

void heartbeatTask(void *parameter) {
  (void)parameter;

  for (;;) {
    digitalWrite(HEARTBEAT_LED_PIN, HIGH);
    vTaskDelay(pdMS_TO_TICKS(HEARTBEAT_TASK_PERIOD_MS / 2));
    digitalWrite(HEARTBEAT_LED_PIN, LOW);
    vTaskDelay(pdMS_TO_TICKS(HEARTBEAT_TASK_PERIOD_MS / 2));
  }
}

void mainDisplayTask(void *parameter) {
  (void)parameter;

  

  for (;;) { // Infinite loop
    vTaskDelay(MAIN_TASK_PERIOD_MS / portTICK_PERIOD_MS);
    
    mainStateMachine();
  }
}

void mainStateMachine(void) {
     
  // Check for enter low power
  // Check for enter/exit sleep

  switch(mainState) {
    case STATE_DEFAULT: {
      // Do nothing, init will take us out of this
      break;
    }
    case STATE_RUNNING: {
      // Update display periodically
      if(millis() - lastDisplayUpdateMs > DISPLAY_UPDATE_PERIOD_MS) {
        // Time to update the display!
        esp_task_wdt_add(NULL); // Start & feed watchdog in case things get weird
        esp_task_wdt_reset();
        drawBmpFromUrl(ucopURL);
        esp_task_wdt_delete(NULL); // Release watchdog so it doesn't trigger while we're between display loops
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