#include "task-supervisor.h"

#define RESET_STATS_MAGIC (0xB05B0A2DUL)

// RTC_NOINIT_ATTR, unlike RTC_DATA_ATTR, isn't re-initialized after a crash or
// watchdog reset, so the counters survive them. Its contents are garbage after
// a power-on, which the magic number check catches.
RTC_NOINIT_ATTR ResetStats resetStats;

void recordResetReason(esp_reset_reason_t resetReason) {
    if (resetReason == ESP_RST_POWERON || resetStats.magic != RESET_STATS_MAGIC) {
        memset(&resetStats, 0, sizeof(resetStats));
        resetStats.magic = RESET_STATS_MAGIC;
    }

    resetStats.wakeCount++;

    switch (resetReason) {
        case ESP_RST_PANIC:
        case ESP_RST_INT_WDT:
        case ESP_RST_TASK_WDT:
        case ESP_RST_WDT:
            resetStats.crashCount++;
            resetStats.lastCrashReason = (int)resetReason;
            break;
        case ESP_RST_BROWNOUT:
            resetStats.brownoutCount++;
            break;
        default:
            break;
    }
}

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