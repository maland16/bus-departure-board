#include "deep-sleep-driver.h"

#include <esp_sleep.h>

#include "rtc-driver.h"

#define MINUTES_TO_MICROSECONDS (60ULL * 1000000ULL)

void deepSleepForMinutes(uint32_t minutes) {
  const uint64_t sleepUs = (uint64_t)minutes * MINUTES_TO_MICROSECONDS; // Convert to microseconds

  Serial.printf("Entering deep sleep for %lu minute(s)\n", (unsigned long)minutes);
  delay(100);

  esp_sleep_enable_timer_wakeup(sleepUs);
  esp_deep_sleep_start();
}

uint32_t getTimeSinceLastUpdate(void) {
  if (lastDisplayUpdateEpoch == 0) {
    return 0;
  }

  const uint32_t nowEpoch = rtc.getEpoch();
  if (nowEpoch <= lastDisplayUpdateEpoch) {
    return 0;
  }

  return nowEpoch - lastDisplayUpdateEpoch;
}