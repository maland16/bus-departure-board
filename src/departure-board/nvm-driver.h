#ifndef NVM_DRIVER_H
#define NVM_DRIVER_H

#include <Arduino.h>

enum DisplayMode {
  DISPLAY_MODE_LIVE_DATA = 0,
  DISPLAY_MODE_NIGHTTIME = 1,
  DISPLAY_MODE_UNAVAILABLE = 2,
};

void initNvmDriver(void);
void setLastDisplayedMode(DisplayMode mode);
DisplayMode getLastDisplayedMode(void);

void setLastDisplayUpdatedEpoch(uint32_t epochSeconds);
uint32_t getLastDisplayUpdatedEpoch(void);

bool appendResetReason(uint32_t resetReason, uint32_t timestampEpochSeconds);
uint8_t getResetReasonCount(void);
bool getResetReasonAt(uint8_t index, uint32_t &resetReason, uint32_t &timestampEpochSeconds);
void printAllPreferences(void);

void clearNvmState(void);

#endif // NVM_DRIVER_H
