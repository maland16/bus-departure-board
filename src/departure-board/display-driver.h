#ifndef DISPLAY_DRIVER_H
#define DISPLAY_DRIVER_H

#include <GxEPD2_BW.h>

void initDisplayDriver(void);

/**
 * @brief Render the "live tracking unavailable" screen with a QR code.
 */
void showUnavailableImage(const char *url, const char *reason);

/**
 * @brief Grab the latest image
 */
bool drawBmpFromUrl(const char *url);

#endif // DISPLAY_DRIVER_H