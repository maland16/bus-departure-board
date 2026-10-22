#ifndef WAKE_STATS_H
#define WAKE_STATS_H

#include <Arduino.h>

// Values are sent in telemetry, so don't renumber them (same as DisplayMode)
enum DisplayResult {
  DISPLAY_RESULT_NOT_ATTEMPTED = 0,
  DISPLAY_RESULT_REFRESHED = 1,
  DISPLAY_RESULT_UNCHANGED = 2,
  DISPLAY_RESULT_DOWNLOAD_FAILED = 3,
  DISPLAY_RESULT_BAD_IMAGE = 4,
};

// What happened during this wake-up, filled in by the drivers as they go and
// reported in telemetry. Starts zeroed on every boot.
struct WakeStats {
  uint32_t wifiConnectMs;
  uint32_t ntpMs;
  uint32_t downloadMs;
  uint32_t refreshMs;
  int httpCode;
  DisplayResult displayResult;
};

extern WakeStats wakeStats;

#endif // WAKE_STATS_H
