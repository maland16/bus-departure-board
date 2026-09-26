#include "deep-sleep-driver.h"

#include <esp_sleep.h>

void initDeepSleepDriver(void) {
  Serial.println("Deep sleep driver initialized");
}

void deepSleepForMinutes(uint32_t minutes) {
  const uint64_t sleepUs = (uint64_t)minutes * 60ULL * 1000000ULL; // Convert to microseconds

  Serial.printf("Entering deep sleep for %lu minute(s)\n", (unsigned long)minutes);
  Serial.println("goodnight!");
  delay(100);

  esp_sleep_enable_timer_wakeup(sleepUs);
  esp_deep_sleep_start();
}
