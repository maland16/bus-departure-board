#include "battery-driver.h"

#define SOLAR_ADC_PIN (A1)
#define SOLAR_DIVIDER_TOP_OHMS (33000.0f)
#define SOLAR_DIVIDER_BOTTOM_OHMS (16500.0f)

#define BATTERY_ADC_PIN (A0)
#define BATTERY_DIVIDER_TOP_OHMS (100000.0f)
#define BATTERY_DIVIDER_BOTTOM_OHMS (100000.0f)

#define BATTERY_CUTOFF_VOLTAGE (2.7f)

// A0/A1 on the ESP32-S3 Feather are ADC2 pins, which can't be read reliably
// while wifi is running. Sample once in initBatteryDriver() (before wifi comes
// up) and serve these cached values from then on.
static float solarVoltage = 0.0f;
static float batteryVoltage = 0.0f;

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

  solarVoltage = readVoltageForPin(SOLAR_ADC_PIN, SOLAR_DIVIDER_TOP_OHMS, SOLAR_DIVIDER_BOTTOM_OHMS);
  batteryVoltage = readVoltageForPin(BATTERY_ADC_PIN, BATTERY_DIVIDER_TOP_OHMS, BATTERY_DIVIDER_BOTTOM_OHMS);
}

float getSolarVoltage(void) {
  return solarVoltage;
}

float getBatteryVoltage(void) {
  return batteryVoltage;
}

void printVoltages(void) {
  Serial.printf("Solar voltage: %.2f V\n", getSolarVoltage());
  Serial.printf("Battery voltage: %.2f V\n", getBatteryVoltage());
}

bool isBatteryBelowCutoffVoltage(void) {
  return getBatteryVoltage() < BATTERY_CUTOFF_VOLTAGE;
}
