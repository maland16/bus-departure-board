#include "display-driver.h"

#include "GxEPD2_display_selection_new_style.h"

#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <ESP32Time.h>
#include <esp_rom_crc.h>

#include <Fonts/FreeSans9pt7b.h>
#include <Fonts/FreeSans12pt7b.h>
#include <Fonts/FreeSans18pt7b.h>
#include <Fonts/FreeSansBold18pt7b.h>
#include <Fonts/FreeSansBold24pt7b.h>

#include "src/third-party/QRCode/src/rm_qrcode.h"

#include "wifi-driver.h"
#include "rtc-driver.h"
#include "wake-stats.h"

// The payload schema this firmware understands (see server/busboard/payload.py)
#define PAYLOAD_VERSION (1)
// Keep showing a departure this long after its estimated time, like the server does
#define PAST_GRACE_S (60)

HTTPClient http;

// ---- Departures from the last good download, kept across deep sleep so the
// board can keep counting down on its own clock if a download fails ----
#define MAX_DEPARTURES (12) // the server sends up to 12
#define BOARD_ROWS (8)      // ...and the board shows the first 8 still to come

struct Departure {
  char route[8];     // e.g. "4", "24A", "BRZ"
  char headsign[40];
  uint32_t est;      // estimated departure, epoch s
  uint32_t sched;    // scheduled departure, epoch s
  uint8_t src;       // 2 predicted for this stop, 1 estimated from another stop, 0 schedule only
};

struct BoardData {
  uint32_t now;      // when the server generated it (epoch s), 0 = no data yet
  bool live;         // false = GP Metro's realtime feed is down, every time is the schedule
  char stop[8];
  char name[40];
  uint8_t count;
  Departure deps[MAX_DEPARTURES];
};

RTC_DATA_ATTR static BoardData board;

// CRC32 of the text currently on the panel, saved across deep sleep so we can
// skip refreshing when nothing changed. Cleared whenever something else is
// drawn over the departures.
RTC_DATA_ATTR static uint32_t lastDrawnCrc = 0;
RTC_DATA_ATTR static bool lastDrawnCrcValid = false;

// ---- Portrait layout, 480 wide x 800 tall (copies server/busboard/render.py) ----
static const int MARGIN = 16;
static const int HEADER_H = 96;
static const int FOOTER_H = 34;

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
    // The board's clock is set from the web host's Date header
    static const char *headerKeys[] = { "Date" };
    http.collectHeaders(headerKeys, 1);
    Serial.println("Display initialized");
}

bool haveBoardData(void) {
  return board.now != 0;
}

uint32_t boardDataEpoch(void) {
  return board.now;
}

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

static bool downloadBoardData(const char *url) {
  client.setInsecure();

  if (!http.begin(client, url)) {
    Serial.println("http.begin() failed - malformed URL?");
    return false;
  }
  Serial.printf("GET %s\n", url);

  const int httpCode = http.GET();
  wakeStats.httpCode = httpCode;
  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("HTTP GET failed: %d (%s)\n", httpCode, http.errorToString(httpCode).c_str());
    http.end();
    return false;
  }

  const bool clockSet = setClockFromHttpDate(http.header("Date").c_str());
  const String body = http.getString();
  http.end();
  Serial.printf("Downloaded %u bytes\n", body.length());

  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, body);
  if (err) {
    Serial.printf("Bad JSON from server: %s\n", err.c_str());
    wakeStats.displayResult = DISPLAY_RESULT_BAD_DATA;
    return false;
  }
  if ((doc["v"] | 0) != PAYLOAD_VERSION || (doc["now"] | 0u) == 0) {
    Serial.printf("Unexpected payload (v=%d, now=%lu)\n", doc["v"] | 0, (unsigned long)(doc["now"] | 0u));
    wakeStats.displayResult = DISPLAY_RESULT_BAD_DATA;
    return false;
  }

  BoardData data = {};
  data.now = doc["now"];
  data.live = doc["live"] | true;
  strlcpy(data.stop, doc["stop"] | "", sizeof(data.stop));
  strlcpy(data.name, doc["name"] | "", sizeof(data.name));
  for (JsonObject d : doc["deps"].as<JsonArray>()) {
    if (data.count >= MAX_DEPARTURES) break;
    Departure &dep = data.deps[data.count++];
    strlcpy(dep.route, d["r"] | "", sizeof(dep.route));
    strlcpy(dep.headsign, d["h"] | "", sizeof(dep.headsign));
    dep.est = d["t"] | 0u;
    dep.sched = d["s"] | dep.est;
    dep.src = d["src"] | 0;
  }
  board = data;

  // Shouldn't happen (any web host sends Date), but without a clock we'd have
  // nothing to count down from, so fall back on the server's (slightly old) time
  if (!clockSet && !getRTCValid()) {
    rtc.setTime(board.now);
    rtcValid = true;
    Serial.println("No Date header; clock set from payload time");
  }

  Serial.printf("Stop %s \"%s\": %u departures, live=%d, data %ld s old\n",
                board.stop, board.name, board.count, board.live,
                (long)rtc.getEpoch() - (long)board.now);
  return true;
}

bool fetchBoardData(const char *url) {
  wakeStats.displayResult = DISPLAY_RESULT_DOWNLOAD_FAILED;
  const uint32_t downloadStartMs = millis();
  const bool ok = downloadBoardData(url);
  wakeStats.downloadMs = millis() - downloadStartMs;
  if (ok) {
    wakeStats.displayResult = DISPLAY_RESULT_NOT_ATTEMPTED; // until drawBoard() runs
  }
  return ok;
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

static void drawCentered(const char *s, int cx, int baselineY) {
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(cx - w / 2 - x1, baselineY);
  display.print(s);
}

static void drawRightAligned(const char *s, int rightX, int baselineY) {
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(rightX - w - x1, baselineY);
  display.print(s);
}

// Advance width of `s` in the current font
static int textWidth(const char *s) {
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  return x1 + w;
}

// Shorten `s` in place with "..." until it fits in maxW in the current font
static void fitText(char *s, size_t size, int maxW) {
  if (textWidth(s) <= maxW) return;
  size_t len = strlen(s);
  char buf[48];
  while (len > 0) {
    len--;
    while (len > 0 && s[len - 1] == ' ') len--; // don't leave "Falmouth ..."
    snprintf(buf, sizeof(buf), "%.*s...", (int)len, s);
    if (textWidth(buf) <= maxW) break;
  }
  strlcpy(s, buf, size);
}

// "7:42 pm", in the board's timezone
static void formatClock(char *out, size_t size, uint32_t epoch) {
  const time_t t = epoch;
  struct tm local;
  localtime_r(&t, &local);
  const int hour12 = local.tm_hour % 12 == 0 ? 12 : local.tm_hour % 12;
  snprintf(out, size, "%d:%02d %s", hour12, local.tm_min, local.tm_hour < 12 ? "am" : "pm");
}

// Everything that ends up on the panel, so it can be CRC'd before drawing
struct Row {
  char route[8];
  char headsign[48];
  char time[12];
  char status[40];
};
struct Screen {
  char stop[16];
  char name[48];
  uint8_t rows;
  Row row[BOARD_ROWS];
  char footer[48];
};

static void buildScreen(Screen &s, uint32_t now, bool clockTimesOnly) {
  memset(&s, 0, sizeof(s)); // zero padding too, it's included in the CRC
  display.setRotation(3);   // portrait upside down on the 800x480 panel, fonts measure in this frame

  snprintf(s.stop, sizeof(s.stop), "STOP %s", board.stop);
  strlcpy(s.name, board.name, sizeof(s.name));
  display.setFont(&FreeSansBold18pt7b);
  fitText(s.name, sizeof(s.name), display.width() - 2 * MARGIN);

  for (uint8_t i = 0; i < board.count && s.rows < BOARD_ROWS; i++) {
    const Departure &d = board.deps[i];
    if ((int64_t)d.est + PAST_GRACE_S < now) continue; // already left
    Row &r = s.row[s.rows++];

    strlcpy(r.route, d.route, sizeof(r.route));

    const int32_t mins = ((int32_t)d.est - (int32_t)now) / 60;
    const bool showClock = clockTimesOnly || mins >= 60;
    if (showClock) {
      formatClock(r.time, sizeof(r.time), d.est);
    } else if (mins <= 0) {
      strlcpy(r.time, "Due", sizeof(r.time));
    } else {
      snprintf(r.time, sizeof(r.time), "%ld min", (long)mins);
    }

    if (d.src == 0) {
      strlcpy(r.status, "scheduled", sizeof(r.status));
    } else {
      // The clock time, unless it's already the big time on the right
      char clock[16] = "";
      if (!showClock) {
        formatClock(clock, sizeof(clock), d.est);
        strlcat(clock, ", ", sizeof(clock));
      }
      const int32_t delayS = (int32_t)d.est - (int32_t)d.sched;
      const int32_t delayMin = (delayS + (delayS >= 0 ? 30 : -30)) / 60; // rounded
      char rel[20];
      if (delayMin == 0) {
        strlcpy(rel, "on time", sizeof(rel));
      } else {
        snprintf(rel, sizeof(rel), "%ld min %s", (long)abs(delayMin), delayMin > 0 ? "late" : "early");
      }
      snprintf(r.status, sizeof(r.status), "%s%s%s", d.src == 1 ? "~" : "", clock, rel);
    }

    // Headsign gets the rest of the line after the route badge, the status
    // whatever's left of the time below it
    display.setFont(&FreeSansBold18pt7b);
    const int badgeW = max(56, textWidth(r.route) + 16);
    const int timeW = textWidth(r.time);
    const int textW = display.width() - 2 * MARGIN - badgeW - 12;
    strlcpy(r.headsign, d.headsign, sizeof(r.headsign));
    display.setFont(&FreeSans18pt7b);
    fitText(r.headsign, sizeof(r.headsign), textW);
    display.setFont(&FreeSans12pt7b);
    fitText(r.status, sizeof(r.status), textW - timeW - 12);
  }

  strlcpy(s.footer, board.live ? "~ estimated" : "Schedule only - live tracking down",
          sizeof(s.footer));
}

static void drawScreen(const Screen &s) {
  const int panelW = display.width();
  const int panelH = display.height();
  const int rowH = (panelH - HEADER_H - FOOTER_H) / BOARD_ROWS;

  display.fillScreen(GxEPD_WHITE);
  display.setTextColor(GxEPD_BLACK);

  // Header
  display.setFont(&FreeSans12pt7b);
  display.setCursor(MARGIN, 32);
  display.print(s.stop);
  display.setFont(&FreeSansBold18pt7b);
  display.setCursor(MARGIN, 74);
  display.print(s.name);
  display.fillRect(MARGIN, HEADER_H - 6, panelW - 2 * MARGIN, 3, GxEPD_BLACK);

  for (uint8_t i = 0; i < s.rows; i++) {
    const Row &r = s.row[i];
    const int y = HEADER_H + i * rowH;

    // First line: route badge (white on black), then the headsign
    display.setFont(&FreeSansBold18pt7b);
    const int badgeW = max(56, textWidth(r.route) + 16);
    const int textX = MARGIN + badgeW + 12;
    display.fillRoundRect(MARGIN, y + 6, badgeW, 40, 4, GxEPD_BLACK);
    display.setTextColor(GxEPD_WHITE);
    drawCentered(r.route, MARGIN + badgeW / 2, y + 39);
    display.setTextColor(GxEPD_BLACK);
    display.setFont(&FreeSans18pt7b);
    display.setCursor(textX, y + 39);
    display.print(r.headsign);

    // Second line: status under the headsign, time right aligned
    display.setFont(&FreeSansBold18pt7b);
    drawRightAligned(r.time, panelW - MARGIN, y + rowH - 9);
    display.setFont(&FreeSans12pt7b);
    display.setCursor(textX, y + rowH - 11);
    display.print(r.status);

    if (i < s.rows - 1) {
      display.drawFastHLine(MARGIN, y + rowH - 2, panelW - 2 * MARGIN, GxEPD_BLACK);
    }
  }

  if (s.rows == 0) {
    display.setFont(&FreeSans18pt7b);
    drawCentered("No departures in", panelW / 2, panelH / 2 - 10);
    drawCentered("the next few hours", panelW / 2, panelH / 2 + 34);
  }

  // Footer
  const int footerY = panelH - FOOTER_H;
  display.drawFastHLine(MARGIN, footerY + 2, panelW - 2 * MARGIN, GxEPD_BLACK);
  display.setFont(&FreeSans9pt7b);
  display.setCursor(MARGIN, panelH - 10);
  display.print(s.footer);
}

void drawBoard(bool clockTimesOnly) {
  const uint32_t now = rtc.getEpoch();
  static Screen screen; // static: ~900 bytes, keep it off the stack
  buildScreen(screen, now, clockTimesOnly);

  Serial.printf("%s, %s\n", screen.stop, screen.name);
  for (uint8_t i = 0; i < screen.rows; i++) {
    const Row &r = screen.row[i];
    Serial.printf("  %-4s %-30s %8s  %s\n", r.route, r.headsign, r.time, r.status);
  }
  Serial.printf("  (%s)\n", screen.footer);

  // A download failure this wake-up is the more useful thing to report, the
  // redraw from cached data still shows up in refresh_ms
  const bool reportResult = wakeStats.displayResult == DISPLAY_RESULT_NOT_ATTEMPTED;

  // E-ink holds its image without power, so if nothing changed skip the
  // refresh and save the power (and panel wear)
  const uint32_t crc = esp_rom_crc32_le(0, (const uint8_t *)&screen, sizeof(screen));
  if (lastDrawnCrcValid && crc == lastDrawnCrc) {
    Serial.println("Departures unchanged since last refresh; skipping display update");
    if (reportResult) wakeStats.displayResult = DISPLAY_RESULT_UNCHANGED;
    display.hibernate();
    return;
  }

  const uint32_t refreshStartMs = millis();
  display.setFullWindow();
  display.firstPage();
  do {
    drawScreen(screen);
  } while (display.nextPage());
  wakeStats.refreshMs = millis() - refreshStartMs;
  if (reportResult) wakeStats.displayResult = DISPLAY_RESULT_REFRESHED;

  lastDrawnCrc = crc;
  lastDrawnCrcValid = true;

  display.hibernate();
  Serial.println("Departures drawn");
}

void showUnavailableImage(const char *url, const char *reason) {
  const uint8_t VERSION = 4;
  const int SCALE = 10;
  static uint8_t qrBuf[200];
  QRCode qr;
  qrcode_initText(&qr, qrBuf, VERSION, ECC_MEDIUM, url);

  display.setRotation(3); // portrait upside down on the 800x480 panel
  display.setFullWindow();

  const int panelW = display.width();
  const int qrPx = qr.size * SCALE;
  const int qrX = (panelW - qrPx) / 2;
  const int qrY = 24;
  const int textCx = panelW / 2;
  const int textStartY = qrY + qrPx + 72;

  const uint32_t refreshStartMs = millis();
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
    drawCentered("unavailable", textCx, textStartY + 58);
    display.setFont(&FreeSans12pt7b);
    drawCentered(reason, textCx, textStartY + 102);
    drawCentered("Scan for bus tracking info", textCx, textStartY + 136);
  } while (display.nextPage());
  wakeStats.refreshMs = millis() - refreshStartMs;

  display.hibernate();

  // The departures are no longer on the panel, so the next ones must be drawn
  lastDrawnCrcValid = false;
}
