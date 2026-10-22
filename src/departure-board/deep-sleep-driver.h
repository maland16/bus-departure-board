#ifndef DEEP_SLEEP_DRIVER_H
#define DEEP_SLEEP_DRIVER_H

#include <Arduino.h>
#include <esp_attr.h>

enum DisplayMode {
  DISPLAY_MODE_NONE = 0,
  DISPLAY_MODE_LIVE_DATA = 1,
  DISPLAY_MODE_NIGHTTIME = 2, // No longer used, kept so the Grafana value mapping stays correct
  DISPLAY_MODE_UNAVAILABLE = 3,
};

extern DisplayMode lastDisplayedMode;
extern uint32_t lastDisplayUpdateEpoch;

uint32_t getTimeSinceLastUpdate(void);

/**
 * @brief Put the ESP32 into deep sleep for a specified number of minutes.
 * This function will not return! This is the end of the line bub
 */
void deepSleepForMinutes(uint32_t minutes);

#endif // DEEP_SLEEP_DRIVER_H
