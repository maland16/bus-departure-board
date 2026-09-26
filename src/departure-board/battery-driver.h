#ifndef BATTERY_DRIVER_H
#define BATTERY_DRIVER_H

void initBatteryDriver(void);
float readBatteryVoltage(void);
bool isBatteryBelowCutoffVoltage(void);

#endif // BATTERY_DRIVER_H
