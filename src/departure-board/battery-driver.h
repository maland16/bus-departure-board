#ifndef BATTERY_DRIVER_H
#define BATTERY_DRIVER_H

#include <Arduino.h>

/**
 * @brief Set up the ADC and sample solar & battery voltages.
 * Must be called before initWifi(), the sense pins are on ADC2 which
 * is unreliable while wifi is on.
 */
void initBatteryDriver(void);

// These return the voltages sampled in initBatteryDriver()
float getSolarVoltage(void);
float getBatteryVoltage(void);
void printVoltages(void);
bool isBatteryBelowCutoffVoltage(void);

#endif // BATTERY_DRIVER_H
