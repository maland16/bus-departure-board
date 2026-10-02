
#include "display-driver.h"

#include "GxEPD2_display_selection_new_style.h"

#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESP32Time.h>

#include <Fonts/FreeMonoBold9pt7b.h>
#include <Fonts/FreeMonoBold12pt7b.h>
#include <Fonts/FreeSans18pt7b.h>
#include <Fonts/FreeSansBold24pt7b.h>
#include <Fonts/FreeSans12pt7b.h>

#include "src/third-party/QRCode/src/rm_qrcode.h"

#include "debug-print.h"
#include "wifi-driver.h"
#include "rtc-driver.h"

#define BITMAP_SIGNATURE (0x4D42) // "BM" in hex
#define MAX_EXPECTED_HEADER_POSITION_BYTES (50)
#define FETCH_IMAGE_TIMEOUT_MS (20000)
#define FETCH_IMAGE_TIMEOUT_EXPIRED(X) (millis() - X > FETCH_IMAGE_TIMEOUT_MS)
#define HTTP_GET_RETRIES (3)

HTTPClient http;

// ---- Fixed target dimensions (native GxEPD2_750_T7 resolution) ----
static const uint16_t PANEL_WIDTH  = 800;
static const uint16_t PANEL_HEIGHT = 480;

static const size_t FRAMEBUFFER_ROW_BYTES = (PANEL_WIDTH + 7) / 8;                 // 100
static const size_t FRAMEBUFFER_SIZE      = FRAMEBUFFER_ROW_BYTES * PANEL_HEIGHT;  // 48000

// Worst case row size across supported bit depths (24bpp is the largest per row)
static const size_t MAX_BMP_ROW_BYTES = ((uint32_t)PANEL_WIDTH * 24 + 31) / 32 * 4; // 2400

// ---- Static buffers, reused across calls ----
static uint8_t framebuffer[FRAMEBUFFER_SIZE];
static uint8_t bmpRowBuf[MAX_BMP_ROW_BYTES];
static bool blackTable[256]; // palette-index -> "is this black?" lookup for 1/8bpp BMPs

static char timeTextBuffer[50];

void initDisplayDriver()
{
    display.init(115200, true, 2, false); // Initialize display

    // don't keep the TCP/TLS connection alive between calls - a reused
    // connection that's gone stale (idle timeout, NAT/firewall drop) can
    // hang indefinitely on write with no error and no timeout, since that
    // happens below the level http.setTimeout() covers
    http.setReuse(false);
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS); // in case the file is served via a CDN/redirect
    http.setTimeout(8000);
    DEBUG_PRINTLN("Display initialized");
}

// ---- Blocking read of exactly `len` bytes from any Stream, with a stall timeout ----
static bool readFully(Stream &s, uint8_t *dst, size_t len, uint32_t timeoutMs = 8000) {
  size_t got = 0;
  uint32_t lastProgress = millis();
  while (got < len) {
    int avail = s.available();
    if (avail > 0) {
      int n = s.readBytes(dst + got, len - got);
      if (n > 0) {
        got += n;
        lastProgress = millis();
        continue;
      }
    }
    if (millis() - lastProgress > timeoutMs) return false; // stalled
    delay(1);
  }
  return true;
}

// ---- Skip `count` bytes forward in the stream ----
static bool skipBytes(Stream &s, uint32_t count) {
  uint8_t buf[32];
  while (count > 0) {
    uint32_t chunk = count > sizeof(buf) ? sizeof(buf) : count;
    if (!readFully(s, buf, chunk)) return false;
    count -= chunk;
  }
  return true;
}

void helloWorld(void)
{
  Serial.println("helloWorld");
  const char text[] = "Hello worlddd!";
  // most e-papers have width < height (portrait) as native orientation, especially the small ones
  // in GxEPD2 rotation 0 is used for native orientation (most TFT libraries use 0 fix for portrait orientation)
  // set rotation to 1 (rotate right 90 degrees) to have enough space on small displays (landscape)
  display.setRotation(1);
  // select a suitable font in Adafruit_GFX
  display.setFont(&FreeMonoBold9pt7b);
  // on e-papers black on white is more pleasant to read
  display.setTextColor(GxEPD_BLACK);
  // Adafruit_GFX has a handy method getTextBounds() to determine the boundary box for a text for the actual font
  int16_t tbx, tby; uint16_t tbw, tbh; // boundary box window
  display.getTextBounds(text, 0, 0, &tbx, &tby, &tbw, &tbh); // it works for origin 0, 0, fortunately (negative tby!)
  // center bounding box by transposition of origin:
  uint16_t x = ((display.width() - tbw) / 2) - tbx;
  uint16_t y = ((display.height() - tbh) / 2) - tby;
  // full window mode is the initial mode, set it anyway
  display.setFullWindow();
  // here we use paged drawing, even if the processor has enough RAM for full buffer
  // so this can be used with any supported processor board.
  // the cost in code overhead and execution time penalty is marginal
  // tell the graphics class to use paged drawing mode
  display.firstPage();
  do
  {
    // this part of code is executed multiple times, as many as needed,
    // in case of full buffer it is executed once
    // IMPORTANT: each iteration needs to draw the same, to avoid strange effects
    // use a copy of values that might change, don't read e.g. from analog or pins in the loop!
    display.fillScreen(GxEPD_WHITE); // set the background to white (fill the buffer with value for white)
    display.setCursor(x, y); // set the postition to start printing text
    display.print(text); // print some text
    // end of part executed multiple times
  }
  // tell the graphics class to transfer the buffer content (page) to the controller buffer
  // the graphics class will command the controller to refresh to the screen when the last page has been transferred
  // returns true if more pages need be drawn and transferred
  // returns false if the last page has been transferred and the screen refreshed for panels without fast partial update
  // returns false for panels with fast partial update when the controller buffer has been written once more, to make the differential buffers equal
  // (for full buffered with fast partial update the (full) buffer is just transferred again, and false returned)
  while (display.nextPage());
  Serial.println("helloWorld done");
}

void addDateTimeToPageBuffer(void)
{
  struct tm timeinfo = rtc.getTimeStruct();
  char * timeText = asctime(&timeinfo);
  strftime(timeTextBuffer, sizeof(timeTextBuffer), "%A, %b %e, %I:%M%p", &timeinfo);
  
  Serial.print("Adding time to screen buffer: ");
  Serial.println(timeTextBuffer);

  // most e-papers have width < height (portrait) as native orientation, especially the small ones
  // in GxEPD2 rotation 0 is used for native orientation (most TFT libraries use 0 fix for portrait orientation)
  // set rotation to 1 (rotate right 90 degrees) to have enough space on small displays (landscape)
  display.setRotation(1);
  // select a suitable font in Adafruit_GFX
  display.setFont(&FreeSans18pt7b);
  display.setTextColor(GxEPD_BLACK);
  // Adafruit_GFX has a handy method getTextBounds() to determine the boundary box for a text for the actual font
  display.setFullWindow();
  display.firstPage();
  do
  {
    display.fillScreen(GxEPD_WHITE); // set the background to white (fill the buffer with value for white)
    display.setCursor(10, 800 - 20); // set the position to start printing text
    display.print(timeTextBuffer);
  }
  // tell the graphics class to transfer the buffer content (page) to the controller buffer
  // the graphics class will command the controller to refresh to the screen when the last page has been transferred
  // returns true if more pages need be drawn and transferred
  // returns false if the last page has been transferred and the screen refreshed for panels without fast partial update
  // returns false for panels with fast partial update when the controller buffer has been written once more, to make the differential buffers equal
  // (for full buffered with fast partial update the (full) buffer is just transferred again, and false returned)
  while (display.nextPage());
  Serial.println("addDateTimeToPageBuffer done");
}

static void drawCentered(const char *s, int cx, int baselineY) {
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(cx - w / 2 - x1, baselineY);
  display.print(s);
}

void showUnavailableImage(const char *url, const char *reason) {
  const uint8_t VERSION = 4;
  const int SCALE = 10;
  static uint8_t qrBuf[200];
  QRCode qr;
  qrcode_initText(&qr, qrBuf, VERSION, ECC_MEDIUM, url);

  display.setRotation(1); // portrait on the 800x480 panel
  display.setFullWindow();

  const int panelW = display.width();
  const int qrPx = qr.size * SCALE;
  const int qrX = (panelW - qrPx) / 2;
  const int qrY = 24;
  const int textCx = panelW / 2;
  const int textStartY = qrY + qrPx + 50;

  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);

    for (int y = 0; y < qr.size; y++) {
      for (int x = 0; x < qr.size; x++) {
        if (qrcode_getModule(&qr, x, y)) {
          display.fillRect(qrX + x * SCALE, qrY + y * SCALE, SCALE, SCALE, GxEPD_BLACK);
        }
      }
    }

    display.setTextColor(GxEPD_BLACK);
    display.setFont(&FreeSansBold24pt7b);
    drawCentered("Live tracking", textCx, textStartY);
    drawCentered("unavailable", textCx, textStartY + 48);
    display.setFont(&FreeSans12pt7b);
    drawCentered(reason, textCx, textStartY + 88);
    drawCentered("Scan for bus tracking info", textCx, textStartY + 118);
  } while (display.nextPage());

  display.hibernate();
}

void clearScreenPowerOff(void) {
  display.firstPage();
  do
  {
    display.fillScreen(GxEPD_WHITE);
  }
  while (display.nextPage());

  display.powerOff();
}

bool drawBmpFromUrl(const char *url) {
  Serial.printf("stack high water mark (beginning of display loop): %u bytes free\n", uxTaskGetStackHighWaterMark(NULL));

  client.setInsecure();

  if (display.width() != PANEL_WIDTH || display.height() != PANEL_HEIGHT) {
    Serial.printf("Display reports %dx%d, expected native %ux%u - check setRotation(0)\n",
                  display.width(), display.height(), PANEL_WIDTH, PANEL_HEIGHT);
    return false;
  }

  if (!http.begin(client, url)) {
    Serial.println("http.begin() failed - malformed URL?");
    return false;
  } else {
    Serial.printf("http.begin() succeeded: %s\n", url);
  }

  Serial.printf("free: %u, largest block: %u\n",
  heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
  heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));

  if (!heap_caps_check_integrity_all(true)) {
    Serial.println("HEAP CORRUPTED before http.GET()");
  }

  int httpCode = http.GET();
  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("HTTP GET failed: %d (%s)\n", httpCode, http.errorToString(httpCode).c_str());
    http.end();
    return false;
  } else {
    Serial.printf("HTTP GET succeeded: %d (%s)\n", httpCode, http.errorToString(httpCode).c_str());
  }

  if (!heap_caps_check_integrity_all(true)) {
    Serial.println("HEAP CORRUPTED after http.GET()");
  }

  WiFiClient *stream = http.getStreamPtr();
  if (!stream) {
    Serial.println("No response stream available");
    http.end();
    return false;
  } else {
    Serial.println("Response stream available");
  }

  // ---- BMP file header (14 bytes) ----
  uint8_t fileHeader[14];
  if (!readFully(*stream, fileHeader, sizeof(fileHeader))) {
    Serial.println("Failed to read BMP file header");
    http.end();
    return false;
  }
  if (fileHeader[0] != 'B' || fileHeader[1] != 'M') {
    Serial.println("Not a BMP file (missing 'BM' signature)");
    http.end();
    return false;
  }
  uint32_t dataOffset = fileHeader[10] | (fileHeader[11] << 8) | (fileHeader[12] << 16) | ((uint32_t)fileHeader[13] << 24);

  // ---- DIB header (assume standard 40-byte BITMAPINFOHEADER) ----
  uint8_t dib[40];
  if (!readFully(*stream, dib, sizeof(dib))) {
    Serial.println("Failed to read DIB header");
    http.end();
    return false;
  }
  uint32_t dibSize      = dib[0]  | (dib[1]  << 8) | (dib[2]  << 16) | ((uint32_t)dib[3]  << 24);
  int32_t  bmpWidth     = (int32_t)(dib[4]  | (dib[5]  << 8) | (dib[6]  << 16) | ((uint32_t)dib[7]  << 24));
  int32_t  bmpHeightRaw = (int32_t)(dib[8]  | (dib[9]  << 8) | (dib[10] << 16) | ((uint32_t)dib[11] << 24));
  uint16_t bpp          = dib[14] | (dib[15] << 8);
  uint32_t compression  = dib[16] | (dib[17] << 8) | (dib[18] << 16) | ((uint32_t)dib[19] << 24);
  uint32_t colorsUsed   = dib[32] | (dib[33] << 8) | (dib[34] << 16) | ((uint32_t)dib[35] << 24);

  bool topDown = bmpHeightRaw < 0;
  uint32_t bmpHeight = topDown ? (uint32_t)(-bmpHeightRaw) : (uint32_t)bmpHeightRaw;

  Serial.printf("BMP %ldx%lu, %u bpp, compression=%lu, colorsUsed=%lu, topDown=%d\n",
                (long)bmpWidth, (unsigned long)bmpHeight, bpp,
                (unsigned long)compression, (unsigned long)colorsUsed, topDown);

  if (compression != 0) {
    Serial.println("Only uncompressed (BI_RGB) BMPs are supported");
    http.end();
    return false;
  }
  if (bpp != 1 && bpp != 8 && bpp != 24) {
    Serial.printf("Unsupported bit depth: %u (only 1, 8, or 24 bpp supported)\n", bpp);
    http.end();
    return false;
  }
  if ((uint32_t)bmpWidth != PANEL_WIDTH || bmpHeight != PANEL_HEIGHT) {
    Serial.printf("BMP size (%ldx%lu) does not match expected %ux%u - aborting\n",
                  (long)bmpWidth, (unsigned long)bmpHeight, PANEL_WIDTH, PANEL_HEIGHT);
    http.end();
    return false;
  }

  // Skip any extra DIB header bytes (e.g. a V4/V5 header instead of the plain 40-byte one)
  if (dibSize > sizeof(dib)) {
    if (!skipBytes(*stream, dibSize - sizeof(dib))) { http.end(); return false; }
  }

  // ---- For 1/8 bpp, read the color palette so we know which index is "black" ----
  uint32_t paletteBytes = 0;
  if (bpp <= 8) {
    uint32_t numColors = colorsUsed != 0 ? colorsUsed : (1u << bpp);
    if (numColors > 256) numColors = 256;
    paletteBytes = numColors * 4;

    uint8_t palette[1024]; // 256 entries * 4 bytes max - small, fine on the stack
    if (!readFully(*stream, palette, paletteBytes)) {
      Serial.println("Failed to read color palette");
      http.end();
      return false;
    }
    for (uint32_t i = 0; i < numColors; i++) {
      uint8_t b = palette[i * 4 + 0];
      uint8_t g = palette[i * 4 + 1];
      uint8_t r = palette[i * 4 + 2];
      uint16_t gray = (r * 299 + g * 587 + b * 114) / 1000;
      blackTable[i] = (gray <= 128);
    }
  }

  // dataOffset is authoritative - skip forward if there's any gap left (rare)
  uint32_t consumedSoFar = 14 + dibSize + paletteBytes;
  if (dataOffset > consumedSoFar) {
    if (!skipBytes(*stream, dataOffset - consumedSoFar)) { http.end(); return false; }
  }

  // General BMP row-padding formula, works for 1/8/24 bpp alike
  const size_t rowBytesIn = (((uint32_t)PANEL_WIDTH * bpp + 31) / 32) * 4;
  if (rowBytesIn > sizeof(bmpRowBuf)) {
    // Can't happen with PANEL_WIDTH=800 and bpp in {1,8,24}, but guard anyway
    // since bmpRowBuf is a fixed-size static buffer.
    Serial.println("BMP row too large for static row buffer");
    http.end();
    return false;
  }

  memset(framebuffer, 0xFF, FRAMEBUFFER_SIZE); // start all-white; we clear bits for black pixels

  const uint8_t threshold = 128; // used only for the 24bpp path; tune if needed

  bool ok = true;

  Serial.print("Downloading BMP");
  for (uint32_t r = 0; r < PANEL_HEIGHT; r++) {
    if(r % 10 == 0) { Serial.print("."); }

    if (!readFully(*stream, bmpRowBuf, rowBytesIn)) {
      Serial.printf("Failed reading BMP row %lu\n", (unsigned long)r);
      ok = false;
      break;
    }

    // BMP rows are bottom-to-top by default; map into the correct output row
    uint32_t outRow = topDown ? r : (PANEL_HEIGHT - 1 - r);
    uint8_t *destRow = framebuffer + outRow * FRAMEBUFFER_ROW_BYTES;
    
    if(r % 10 == 0) { Serial.print("`"); }

    for (uint16_t x = 0; x < PANEL_WIDTH; x++) {
      bool isBlack;

      if (bpp == 24) {
        uint8_t b  = bmpRowBuf[x * 3 + 0];
        uint8_t g  = bmpRowBuf[x * 3 + 1];
        uint8_t rr = bmpRowBuf[x * 3 + 2];
        uint16_t gray = (rr * 299 + g * 587 + b * 114) / 1000;
        isBlack = (gray <= threshold);
      } else if (bpp == 8) {
        uint8_t index = bmpRowBuf[x];
        isBlack = blackTable[index];
      } else { // bpp == 1
        uint8_t byteVal = bmpRowBuf[x / 8];
        uint8_t index = (byteVal >> (7 - (x % 8))) & 0x1;
        isBlack = blackTable[index];
      }

      if (isBlack) {
        destRow[x / 8] &= ~(0x80 >> (x % 8)); // clear the bit for black
      }
      // else: leave bit set (white) - buffer was pre-filled with 0xFF
    }
  }
  Serial.println(" download complete, closing HTTP connection");

  if (!heap_caps_check_integrity_all(true)) {
    Serial.println("HEAP CORRUPTED right before http.end()");
  }

  http.end();

  if (!heap_caps_check_integrity_all(true)) {
    Serial.println("HEAP CORRUPTED right after http.end()");
  }

  if (!ok) {
    return false;
  }

  display.writeImage(framebuffer, 0, 0, PANEL_WIDTH, PANEL_HEIGHT); // flip to writeImage(..., true) if colors look inverted

  display.refresh();

  Serial.println("Image drawn");

  if (!heap_caps_check_integrity_all(true)) {
    Serial.println("HEAP CORRUPTED end of display function");
  }

  Serial.printf("stack high water mark (end of display loop): %u bytes free\n", uxTaskGetStackHighWaterMark(NULL));
  return true;
}