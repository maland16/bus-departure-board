#include "battery-driver.h"

#define SOLAR_ADC_PIN (A0)
#define SOLAR_DIVIDER_TOP_OHMS (33000.0f)
#define SOLAR_DIVIDER_BOTTOM_OHMS (16500.0f)

#define REGULATOR_ADC_PIN (A1)
#define REGULATOR_DIVIDER_TOP_OHMS (5600.0f)
#define REGULATOR_DIVIDER_BOTTOM_OHMS (33000.0f)

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
  pinMode(REGULATOR_ADC_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(SOLAR_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(REGULATOR_ADC_PIN, ADC_11db);
}

float readSolarVoltage(void) {
  return readVoltageForPin(SOLAR_ADC_PIN, SOLAR_DIVIDER_TOP_OHMS, SOLAR_DIVIDER_BOTTOM_OHMS);
}

float readRegulatorVoltage(void) {
  return readVoltageForPin(REGULATOR_ADC_PIN, REGULATOR_DIVIDER_TOP_OHMS, REGULATOR_DIVIDER_BOTTOM_OHMS);
}

float getSolarVoltage(void) {
  return readSolarVoltage();
}

float getRegulatorVoltage(void) {
  return readRegulatorVoltage();
}

void printVoltages(void) {
  Serial.printf("Solar voltage: %.2f V\n", getSolarVoltage());
  Serial.printf("Regulator voltage: %.2f V\n", getRegulatorVoltage());
}

bool isBatteryBelowCutoffVoltage(void) {
  return getRegulatorVoltage() < BATTERY_CUTOFF_VOLTAGE;
}
