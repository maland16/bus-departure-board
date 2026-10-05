#include "battery-driver.h"

#define SOLAR_ADC_PIN (A1)
#define SOLAR_DIVIDER_TOP_OHMS (33000.0f)
#define SOLAR_DIVIDER_BOTTOM_OHMS (16500.0f)

#define BATTERY_ADC_PIN (A0)
#define BATTERY_DIVIDER_TOP_OHMS (100000.0f)
#define BATTERY_DIVIDER_BOTTOM_OHMS (100000.0f)

#define BATTERY_CUTOFF_VOLTAGE (2.4f)

static float readVoltageForPin(uint8_t pin, float topOhms, float bottomOhms) {
  const int adcReadingMv = analogReadMilliVolts(pin);
  if (adcReadingMv <= 0) {
    return 0.0f;
  }

  const float dividerRatio = (topOhms + bottomOhms) / bottomOhms;
  return (adcReadingMv / 1000.0f) * dividerRatio;
}

void initBatteryDriver(void) {
  pinMode(SOLAR_ADC_PIN, INPUT);
  pinMode(BATTERY_ADC_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(SOLAR_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(BATTERY_ADC_PIN, ADC_11db);
}

float getSolarVoltage(void) {
  return readVoltageForPin(SOLAR_ADC_PIN, SOLAR_DIVIDER_TOP_OHMS, SOLAR_DIVIDER_BOTTOM_OHMS);
}

float getBatteryVoltage(void) {
  return readVoltageForPin(BATTERY_ADC_PIN, BATTERY_DIVIDER_TOP_OHMS, BATTERY_DIVIDER_BOTTOM_OHMS);
}

void printVoltages(void) {
  Serial.printf("Solar voltage: %.2f V\n", getSolarVoltage());
  Serial.printf("Battery voltage: %.2f V\n", getBatteryVoltage());
}

bool isBatteryBelowCutoffVoltage(void) {
  return false; // TEMP

  return getBatteryVoltage() < BATTERY_CUTOFF_VOLTAGE;
}
