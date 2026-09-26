
#include "rtc-driver.h"

#define CLOCK_TIMEZONE ("EST5EDT,M3.2.0,M11.1.0")

ESP32Time rtc(0);
bool rtcValid = false;

bool getRTCValid() {
  return rtcValid;
}

void initClock()
{
  configTzTime(CLOCK_TIMEZONE, "pool.ntp.org", "time.nist.gov");

  unsigned long time = 0;
  uint8_t timeout = 10;

  do {
    time = getEpochTimeFromNPT();
    timeout--;
    delay(100);
  } while(time == 0 && timeout > 0);

  if (time != 0) {
    rtc.setTime(time);
    rtcValid = true;
  } else {
    rtcValid = false;
  }
}

unsigned long getEpochTimeFromNPT()
{
  time_t now;
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) {
    return(0);
  }
  time(&now);
  return now;
}
