#include "nvm-driver.h"

#include <Preferences.h>

namespace {
constexpr const char *kNamespace = "bus_board";
constexpr const char *kLastDisplayedModeKey = "last_display_mode";
constexpr const char *kLastDisplayUpdatedKey = "last_update_epoch";
constexpr const char *kResetCountKey = "reset_count";
constexpr uint8_t kMaxResetHistory = 10U;

Preferences preferences;

String buildReasonKey(uint8_t index) {
  char key[16];
  snprintf(key, sizeof(key), "reset_reason_%u", index);
  return String(key);
}

String buildTimestampKey(uint8_t index) {
  char key[16];
  snprintf(key, sizeof(key), "reset_time_%u", index);
  return String(key);
}

bool isOpen() {
  return preferences.isKey(kLastDisplayedModeKey);
}
}

void initNvmDriver(void) {
  if (!preferences.begin(kNamespace, false)) {
    Serial.println("Failed to initialize NVM storage");
  }
}

void setLastDisplayedMode(DisplayMode mode) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    preferences.begin(kNamespace, false);
  }

  const uint8_t currentValue = preferences.getUChar(kLastDisplayedModeKey, UINT8_MAX);
  const uint8_t newValue = static_cast<uint8_t>(mode);

  if (currentValue == newValue) {
    return;
  }

  preferences.putUChar(kLastDisplayedModeKey, newValue);
}

DisplayMode getLastDisplayedMode(void) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    initNvmDriver();
  }

  return static_cast<DisplayMode>(preferences.getUChar(kLastDisplayedModeKey, DISPLAY_MODE_LIVE_DATA));
}

void setLastDisplayUpdatedEpoch(uint32_t epochSeconds) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    preferences.begin(kNamespace, false);
  }

  const uint32_t currentValue = preferences.getUInt(kLastDisplayUpdatedKey, UINT32_MAX);
  if (currentValue == epochSeconds) {
    return;
  }

  preferences.putUInt(kLastDisplayUpdatedKey, epochSeconds);
}

uint32_t getLastDisplayUpdatedEpoch(void) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    initNvmDriver();
  }

  return preferences.getUInt(kLastDisplayUpdatedKey, 0U);
}

bool appendResetReason(uint32_t resetReason, uint32_t timestampEpochSeconds) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    preferences.begin(kNamespace, false);
  }

  uint8_t count = preferences.getUChar(kResetCountKey, 0U);

  if (count == 0U) {
    preferences.putUInt(buildReasonKey(0).c_str(), resetReason);
    preferences.putUInt(buildTimestampKey(0).c_str(), timestampEpochSeconds);
    preferences.putUChar(kResetCountKey, 1U);
    return true;
  }

  const uint8_t lastIndex = count - 1U;
  const uint32_t currentReason = preferences.getUInt(buildReasonKey(lastIndex).c_str(), UINT32_MAX);
  const uint32_t currentTimestamp = preferences.getUInt(buildTimestampKey(lastIndex).c_str(), UINT32_MAX);

  if (currentReason == resetReason && currentTimestamp == timestampEpochSeconds) {
    return true;
  }

  if (count >= kMaxResetHistory) {
    for (uint8_t i = 0; i < (kMaxResetHistory - 1); ++i) {
      const uint8_t sourceIndex = i + 1;
      const String reasonKey = buildReasonKey(sourceIndex);
      const String timeKey = buildTimestampKey(sourceIndex);

      const uint32_t sourceReason = preferences.getUInt(reasonKey.c_str(), 0U);
      const uint32_t sourceTimestamp = preferences.getUInt(timeKey.c_str(), 0U);

      preferences.putUInt(buildReasonKey(i).c_str(), sourceReason);
      preferences.putUInt(buildTimestampKey(i).c_str(), sourceTimestamp);
    }

    count = kMaxResetHistory - 1U;
  }

  const uint8_t insertIndex = count;
  if (preferences.getUInt(buildReasonKey(insertIndex).c_str(), UINT32_MAX) == resetReason &&
      preferences.getUInt(buildTimestampKey(insertIndex).c_str(), UINT32_MAX) == timestampEpochSeconds) {
    return true;
  }

  preferences.putUInt(buildReasonKey(insertIndex).c_str(), resetReason);
  preferences.putUInt(buildTimestampKey(insertIndex).c_str(), timestampEpochSeconds);
  preferences.putUChar(kResetCountKey, count + 1U);

  return true;
}

uint8_t getResetReasonCount(void) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    initNvmDriver();
  }

  return preferences.getUChar(kResetCountKey, 0U);
}

bool getResetReasonAt(uint8_t index, uint32_t &resetReason, uint32_t &timestampEpochSeconds) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    initNvmDriver();
  }

  const uint8_t count = preferences.getUChar(kResetCountKey, 0U);
  if (index >= count) {
    return false;
  }

  resetReason = preferences.getUInt(buildReasonKey(index).c_str(), 0U);
  timestampEpochSeconds = preferences.getUInt(buildTimestampKey(index).c_str(), 0U);
  return true;
}

void printAllPreferences(void) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    initNvmDriver();
  }

  Serial.println("--- NVM Preferences ---");
  Serial.printf("last_display_mode=%u\n", preferences.getUChar(kLastDisplayedModeKey, 0U));
  Serial.printf("last_update_epoch=%u\n", preferences.getUInt(kLastDisplayUpdatedKey, 0U));
  Serial.printf("reset_count=%u\n", preferences.getUChar(kResetCountKey, 0U));

  const uint8_t count = preferences.getUChar(kResetCountKey, 0U);
  for (uint8_t i = 0; i < count && i < kMaxResetHistory; ++i) {
    uint32_t resetReason = 0;
    uint32_t timestamp = 0;
    if (getResetReasonAt(i, resetReason, timestamp)) {
      Serial.printf("reset_%u => reason=%lu, timestamp=%lu\n", i, (unsigned long)resetReason, (unsigned long)timestamp);
    }
  }

  Serial.println("--- End NVM Preferences ---");
}

void clearNvmState(void) {
  if (!preferences.isKey(kLastDisplayedModeKey)) {
    preferences.begin(kNamespace, false);
  }

  preferences.remove(kLastDisplayedModeKey);
  preferences.remove(kLastDisplayUpdatedKey);
  preferences.remove(kResetCountKey);

  for (uint8_t i = 0; i < kMaxResetHistory; ++i) {
    preferences.remove(buildReasonKey(i).c_str());
    preferences.remove(buildTimestampKey(i).c_str());
  }
}
