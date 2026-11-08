#include "rtc-driver.h"

#include <string.h>

#define CLOCK_TIMEZONE ("EST5EDT,M3.2.0,M11.1.0")

ESP32Time rtc(0);
RTC_DATA_ATTR bool rtcValid = false; // Save in RTC memory so it persists across deep sleep cycles

bool getRTCValid() {
  return rtcValid;
}

void initClock()
{
  // The clock itself is set from each data download, see setClockFromHttpDate()
  setenv("TZ", CLOCK_TIMEZONE, 1);
  tzset();

  Serial.printf("Current time: %04d-%02d-%02d %02d:%02d:%02d\n",
  rtc.getYear(), rtc.getMonth() + 1, rtc.getDay(),
  rtc.getHour(true), rtc.getMinute(), rtc.getSecond());
}

// Days since 1970-01-01 for a civil date (Howard Hinnant's days_from_civil).
// newlib has no timegm(), and mktime() would apply the local timezone.
static int32_t daysFromCivil(int32_t y, uint32_t m, uint32_t d)
{
  y -= m <= 2;
  const int32_t era = (y >= 0 ? y : y - 399) / 400;
  const uint32_t yoe = (uint32_t)(y - era * 400);
  const uint32_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const uint32_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (int32_t)doe - 719468;
}

bool setClockFromHttpDate(const char *date)
{
  // e.g. "Sun, 08 Nov 2026 00:32:47 GMT" (RFC 7231 IMF-fixdate)
  static const char MONTHS[] = "JanFebMarAprMayJunJulAugSepOctNovDec";
  int day, year, hour, minute, second;
  char month[4];
  if (date == NULL ||
      sscanf(date, "%*3s, %d %3s %d %d:%d:%d", &day, month, &year, &hour, &minute, &second) != 6) {
    Serial.printf("Couldn't parse HTTP Date header: \"%s\"\n", date ? date : "");
    return false;
  }
  const char *m = strstr(MONTHS, month);
  if (m == NULL || strlen(month) != 3 || year < 2024) {
    Serial.printf("Couldn't parse HTTP Date header: \"%s\"\n", date);
    return false;
  }

  const uint32_t epoch = (uint32_t)daysFromCivil(year, (m - MONTHS) / 3 + 1, day) * 86400UL
                         + hour * 3600UL + minute * 60UL + second;
  const int32_t correction = (int32_t)(epoch - rtc.getEpoch());
  rtc.setTime(epoch);
  if (rtcValid) {
    Serial.printf("Clock set from server, corrected by %ld s\n", (long)correction);
  } else {
    Serial.println("Clock set from server");
  }
  rtcValid = true;

  Serial.printf("Current time: %04d-%02d-%02d %02d:%02d:%02d\n",
  rtc.getYear(), rtc.getMonth() + 1, rtc.getDay(),
  rtc.getHour(true), rtc.getMinute(), rtc.getSecond());
  return true;
}
