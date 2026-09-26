#ifndef RTC_DRIVER_H
#define RTC_DRIVER_H

#include <time.h>
#include <ESP32Time.h>

extern ESP32Time rtc;
extern bool rtcValid;

void initClock();
unsigned long getEpochTimeFromNPT();
void updateRTCFromNPT();
bool getRTCValid();

#endif // RTC_DRIVER_H