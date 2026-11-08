#ifndef DISPLAY_DRIVER_H
#define DISPLAY_DRIVER_H

#include <GxEPD2_BW.h>

void initDisplayDriver(void);

/**
 * @brief Render the "live tracking unavailable" screen with a QR code.
 */
void showUnavailableImage(const char *url, const char *reason);

/**
 * @brief Download the stop's departures (JSON) from the board server and set
 * the clock from the response. Keeps the previous departures on failure.
 */
bool fetchBoardData(const char *url);

/**
 * @brief Whether there are departures to draw (from this or an earlier wake-up).
 */
bool haveBoardData(void);

/**
 * @brief When the server generated the departures we have (epoch s).
 */
uint32_t boardDataEpoch(void);

/**
 * @brief Draw the departures, counting down from the current time. With
 * clockTimesOnly every time is shown as a clock time (for overnight).
 * Skips the refresh if the screen would look the same.
 */
void drawBoard(bool clockTimesOnly);

#endif // DISPLAY_DRIVER_H
