#ifndef RTC_DRIVER_H
#define RTC_DRIVER_H

#include <time.h>
#include <esp_attr.h>
#include <ESP32Time.h>

extern ESP32Time rtc;
extern RTC_DATA_ATTR bool rtcValid;

void initClock();
unsigned long getEpochTimeFromNPT();
void updateRTCFromNPT();
bool getRTCValid();

#endif // RTC_DRIVER_H