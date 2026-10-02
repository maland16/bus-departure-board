#include "deep-sleep-driver.h"

#include <esp_sleep.h>

#define MINUTES_TO_MICROSECONDS (60ULL * 1000000ULL)

void deepSleepForMinutes(uint32_t minutes) {
  const uint64_t sleepUs = (uint64_t)minutes * MINUTES_TO_MICROSECONDS; // Convert to microseconds

  Serial.printf("Entering deep sleep for %lu minute(s)\n", (unsigned long)minutes);
  delay(100);

  esp_sleep_enable_timer_wakeup(sleepUs);
  esp_deep_sleep_start();
}
