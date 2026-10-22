
#include "rtc-driver.h"

#include <WiFi.h>

#define CLOCK_TIMEZONE ("EST5EDT,M3.2.0,M11.1.0")
#define NTP_SYNC_TIMEOUT_MS (10000)
#define NTP_SERVER_PRIMARY "pool.ntp.org"
#define NTP_SERVER_SECONDARY "time.nist.gov"

ESP32Time rtc(0);
RTC_DATA_ATTR bool rtcValid = false; // Save in RTC memory so it persists across deep sleep cycles

bool getRTCValid() {
  return rtcValid;
}

void initClock()
{
  // Only set the timezone here, SNTP is started in updateRTCFromNPT() once
  // wifi is up (see the comment there)
  setenv("TZ", CLOCK_TIMEZONE, 1);
  tzset();

  Serial.printf("Current time: %04d-%02d-%02d %02d:%02d:%02d\n",
  rtc.getYear(), rtc.getMonth(), rtc.getDay(),
  rtc.getHour(true), rtc.getMinute(), rtc.getSecond());
}


void updateRTCFromNPT()
{
  // Workaround for an arduino-esp32 3.x bug: the first hostByName() after boot
  // calls dns_clear_cache() without holding the lwIP core lock. If SNTP has a
  // DNS lookup in flight at that moment, its callback runs from our task and
  // trips an lwIP assert (udp_new_ip_type "Required to lock TCPIP core"),
  // panicking the board. Doing our own lookup first gets that one-time cache
  // clear out of the way while nothing is pending, and warms the DNS cache for
  // SNTP. So: never start SNTP before this lookup.
  IPAddress ntpServerIp;
  WiFi.hostByName(NTP_SERVER_PRIMARY, ntpServerIp);
  configTzTime(CLOCK_TIMEZONE, NTP_SERVER_PRIMARY, NTP_SERVER_SECONDARY);

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

