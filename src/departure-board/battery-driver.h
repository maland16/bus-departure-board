#ifndef BATTERY_DRIVER_H
#define BATTERY_DRIVER_H

#include <Arduino.h>

void initBatteryDriver(void);
float getSolarVoltage(void);
float getBatteryVoltage(void);
void printVoltages(void);
bool isBatteryBelowCutoffVoltage(void);

#endif // BATTERY_DRIVER_H
