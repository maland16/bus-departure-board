#ifndef RTC_DRIVER_H
#define RTC_DRIVER_H

#include <time.h>
#include <ESP32Time.h>

extern ESP32Time rtc;

void initClock();
unsigned long getEpochTimeFromNPT();
void updateRTCFromNPT();

#endif // RTC_DRIVER_H