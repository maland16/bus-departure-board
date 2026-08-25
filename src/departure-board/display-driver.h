#ifndef DISPLAY_DRIVER_H
#define DISPLAY_DRIVER_H

#include <GxEPD2_BW.h>
#include "GxEPD2_display_selection_new_style.h"

void initDisplay();

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
bool connectAndGetImage(void);

#endif // DISPLAY_DRIVER_H