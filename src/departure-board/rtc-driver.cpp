
#include "rtc-driver.h"

#define CLOCK_TIMEZONE ("EST5EDT,M3.2.0,M11.1.0")
#define NTP_SYNC_TIMEOUT_MS (10000)

ESP32Time rtc(0);
RTC_DATA_ATTR bool rtcValid = false; // Save in RTC memory so it persists across deep sleep cycles

bool getRTCValid() {
  return rtcValid;
}

void initClock()
{
  configTzTime(CLOCK_TIMEZONE, "pool.ntp.org", "time.nist.gov");

  Serial.printf("Current time: %04d-%02d-%02d %02d:%02d:%02d\n",
  rtc.getYear(), rtc.getMonth(), rtc.getDay(),
  rtc.getHour(true), rtc.getMinute(), rtc.getSecond());
}


void updateRTCFromNPT()
{
  const unsigned long time = getEpochTimeFromNPT();

  if (time != 0) {
    rtc.setTime(time);
    rtcValid = true;
  }

  Serial.printf("Current time: %04d-%02d-%02d %02d:%02d:%02d\n",
  rtc.getYear(), rtc.getMonth(), rtc.getDay(),
  rtc.getHour(true), rtc.getMinute(), rtc.getSecond());
}

unsigned long getEpochTimeFromNPT()
{
  time_t now;
  struct tm timeinfo;
  // getLocalTime() polls until the time looks valid or the timeout expires
  if (!getLocalTime(&timeinfo, NTP_SYNC_TIMEOUT_MS)) {
    return(0);
  }
  time(&now);
  return now;
}

