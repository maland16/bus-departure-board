#include "battery-driver.h"
#include <Arduino.h>

#define BATTERY_ADC_PIN (A1)
#define BATTERY_DIVIDER_TOP_OHMS (100000.0f)
#define BATTERY_DIVIDER_BOTTOM_OHMS (100000.0f)
#define BATTERY_CUTOFF_VOLTAGE (2.7f)

void initBatteryDriver(void) {
  pinMode(BATTERY_ADC_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(BATTERY_ADC_PIN, ADC_11db);
}

float readBatteryVoltage(void) {
  const int adcReadingMv = analogReadMilliVolts(BATTERY_ADC_PIN);

  if (adcReadingMv <= 0) {
    return 0.0f;
  }

  const float dividerRatio =
      (BATTERY_DIVIDER_TOP_OHMS + BATTERY_DIVIDER_BOTTOM_OHMS) /
      BATTERY_DIVIDER_BOTTOM_OHMS;

  return (adcReadingMv / 1000.0f) * dividerRatio;
}

bool isBatteryBelowCutoffVoltage(void) {
    return false; // TEMP until battery is connected and stuff
  
    // return readBatteryVoltage() < BATTERY_CUTOFF_VOLTAGE;
}
