#ifndef DEEP_SLEEP_DRIVER_H
#define DEEP_SLEEP_DRIVER_H

#include <Arduino.h>

/**
 * @brief Put the ESP32 into deep sleep for a specified number of minutes.
 * This function will not return! This is the end of the line bub
 */
void deepSleepForMinutes(uint32_t minutes);

#endif // DEEP_SLEEP_DRIVER_H
