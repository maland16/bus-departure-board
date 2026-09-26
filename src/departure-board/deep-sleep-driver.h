#ifndef DEEP_SLEEP_DRIVER_H
#define DEEP_SLEEP_DRIVER_H

#include <Arduino.h>

void initDeepSleepDriver(void);
void deepSleepForMinutes(uint32_t minutes);

#endif // DEEP_SLEEP_DRIVER_H
