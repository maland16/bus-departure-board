#include "task-supervisor.h"

// Define a structured representation of our configured tasks to calculate percentage margins.
// Since uxTaskGetStackHighWaterMark() returns the MINIMUM remaining space since the task started,
// we can compare that remaining space against the total allocated stack depth.
typedef struct {
    TaskHandle_t handle;
    const char *name;
    uint32_t allocatedStackDepth; // In words or bytes depending on the platform architecture
} TrackedTask_t;

#define SUPERVISOR_CHECK_INTERVAL_MS (5000)

// Example array of tasks to track. In a real application, populate this array 
// with the handles and stack sizes returned during xTaskCreate.
#define MAX_TRACKED_TASKS 3
static TrackedTask_t trackedTasks[MAX_TRACKED_TASKS];
static uint8_t trackedTaskCount = 0;

/**
 * @brief Registers a task with the supervisor for high-water mark percentage tracking.
 * 
 * @param handle The FreeRTOS TaskHandle_t
 * @param name String identifier for logging
 * @param stackDepth Total stack allocated (e.g., 256, 2048) during xTaskCreate
 */
void registerTaskWithSupervisor(TaskHandle_t handle, const char *name, uint32_t stackDepth) {
    if (trackedTaskCount < MAX_TRACKED_TASKS) {
        trackedTasks[trackedTaskCount].handle = handle;
        trackedTasks[trackedTaskCount].name = name;
        trackedTasks[trackedTaskCount].allocatedStackDepth = stackDepth;
        trackedTaskCount++;
    }
}

/**
 * @brief Supervisor task that routinely inspects the stack margin of registered tasks.
 * Alerts via Serial if the lowest watermark dips below 20% of its initial capacity.
 */
void vSupervisorTask(void *pvParameters) {
    (void) pvParameters;

    for (;;) {
        for (uint8_t i = 0; i < trackedTaskCount; i++) {
            if (trackedTasks[i].handle != NULL) {
                // Get the lowest remaining stack recorded for this task
                UBaseType_t highWaterMark = uxTaskGetStackHighWaterMark(trackedTasks[i].handle);
                
                // Calculate 20% threshold of the allocated stack size
                uint32_t warningThreshold = (trackedTasks[i].allocatedStackDepth * 20) / 100;
                
                // Alert if remaining room falls under the 20% limit
                if (highWaterMark <= warningThreshold) {
                    Serial.print(F("[SUPERVISOR WARNING] Task '"));
                    Serial.print(trackedTasks[i].name);
                    Serial.print(F("' is running out of stack! Only "));
                    Serial.print(highWaterMark);
                    Serial.print(F(" units left out of "));
                    Serial.print(trackedTasks[i].allocatedStackDepth);
                    Serial.println(F(". (Under 20% margin)"));
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(SUPERVISOR_CHECK_INTERVAL_MS));
    }
}

/**
 * @brief FreeRTOS Callback Hook for severe Stack Overflow events.
 * Triggered automatically by the kernel when configCHECK_FOR_STACK_OVERFLOW is set to 1 or 2.
 */
extern "C" void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName) {
    // Disable interrupts instantly to isolate system state and prevent further corruption
    taskDISABLE_INTERRUPTS();
    
    // Force immediate critical feedback to the developer console
    Serial.println(F("\n\n##################################################"));
    Serial.print(F("!!! CRITICAL STACK OVERFLOW DETECTED IN TASK: "));
    Serial.println(pcTaskName);
    Serial.println(F("System halted. Please increase stack allocation."));
    Serial.println(F("##################################################"));
    
    // Trap execution permanently to allow hardware debugging attached states
    while (1) {
        yield(); 
    }
}

/*
// Registers the current task with the ESP-IDF task watchdog and sets a
// hard reboot ceiling. Call this ONCE from setup(), from whichever task
// will call drawBmpFromUrl() (the default Arduino loop task if that's
// where you call it from). This is a last-resort safety net: if the
// device ever blocks indefinitely somewhere below our own timeouts (a
// stale connection, a stuck TLS handshake, a wedged driver call), the
// watchdog reboots it rather than hanging forever silently.
void initTaskWatchdog() {
#if ESP_IDF_VERSION_MAJOR >= 5
  esp_task_wdt_config_t twdtConfig = {
    .timeout_ms = APP_WDT_TIMEOUT_S * 1000,
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  esp_task_wdt_init(&twdtConfig);
#else
  esp_task_wdt_init(APP_WDT_TIMEOUT_S, true ); // panic (reboot) on timeout
#endif
  esp_task_wdt_add(NULL); // subscribe the calling task
}
*/

void printResetReason( esp_reset_reason_t resetReason ) {
    switch (resetReason) {
        case ESP_RST_POWERON:   
            Serial.println("Reset due to power-on event");
            break;
        case ESP_RST_SW:        
            Serial.println("Software reset via esp_restart()");
            break;
        case ESP_RST_PANIC: 
            Serial.println("Software reset due to exception/panic");
            break;
        case ESP_RST_INT_WDT: 
            Serial.println("Interrupt watchdog reset");
            break;
        case ESP_RST_TASK_WDT:  
            Serial.println("Task watchdog reset");
            break;
        case ESP_RST_DEEPSLEEP:
            Serial.println("Reset after exiting deep sleep");
            break;
        case ESP_RST_BROWNOUT: 
            Serial.println("Brownout reset");
            break;
        default:
            Serial.println("Reset reason other or unknown");
            break;
    }
}