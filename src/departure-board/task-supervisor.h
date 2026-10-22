#ifndef SUPERVISOR_H
#define SUPERVISOR_H

#include <Arduino.h>
#include <FreeRTOS.h>
#include <task.h>
#include <esp_system.h>
#include <esp_attr.h>

// Counts since the last power-on, kept across deep sleep AND crashes/watchdog
// resets so they can be reported in telemetry
typedef struct {
    uint32_t magic;           // marks the struct as initialized, see recordResetReason()
    uint32_t wakeCount;       // every boot, drops back to 0 on power loss
    uint32_t crashCount;      // panics and watchdog resets
    uint32_t brownoutCount;
    int lastCrashReason;      // esp_reset_reason_t of the most recent crash
} ResetStats;

extern ResetStats resetStats;

void registerTaskWithSupervisor(TaskHandle_t handle, const char *name, uint32_t stackDepth);
void vSupervisorTask(void *pvParameters);
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName);
void printResetReason(esp_reset_reason_t resetReason);

/**
 * @brief Update resetStats for this boot. Call once at the start of setup().
 */
void recordResetReason(esp_reset_reason_t resetReason);


#endif // SUPERVISOR_H
