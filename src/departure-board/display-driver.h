#ifndef DISPLAY_DRIVER_H
#define DISPLAY_DRIVER_H

#include <GxEPD2_BW.h>

void initDisplayDriver(void);

void helloWorld(void);

/**
 * @brief Add Date and "last updated" time to page buffer
 */
void addDateTimeToPageBuffer(void);

/**
 * @brief Clear the screen to white and tell the display to power down
 */
void clearScreenPowerOff(void);

/**
 * @brief Grab the latest image
 */
bool drawBmpFromUrl(const char *url);

#endif // DISPLAY_DRIVER_H