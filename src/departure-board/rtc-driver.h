#ifndef RTC_DRIVER_H
#define RTC_DRIVER_H

#include <time.h>
#include <esp_attr.h>
#include <ESP32Time.h>

extern ESP32Time rtc;
extern bool rtcValid;

void initClock();
bool getRTCValid();

/**
 * @brief Set the clock from an HTTP Date header (always UTC), e.g.
 * "Sun, 08 Nov 2026 00:32:47 GMT". The board has no other time source.
 */
bool setClockFromHttpDate(const char *date);

#endif // RTC_DRIVER_H
