
#include "display-driver.h"

#include <WiFiClientSecure.h>
#include <ESP32Time.h>

#include <Fonts/FreeMonoBold9pt7b.h>
#include <Fonts/FreeMonoBold12pt7b.h>
#include <Fonts/FreeSans18pt7b.h>

#include "debug-print.h"
#include "wifi-driver.h"
#include "rtc-driver.h"

#define BITMAP_SIGNATURE (0x4D42) // "BM" in hex
#define MAX_EXPECTED_HEADER_POSITION_BYTES (50)

static const int httpPort  = 80;
static const int httpsPort = 443;
static const char* hostname   = "www.devinmalanaphy.com";
static const char* path = "/wp-content/uploads/2026/01/temp.bmp";

static char timeTextBuffer[50];

void initDisplay()
{
    display.init(115200, true, 2, false); // Initialize display
}

uint32_t read8n(WiFiClient& client, uint8_t* buffer, int32_t bytes)
{
  int32_t remain = bytes;
  uint32_t start = millis();
  while ((client.connected() || client.available()) && (remain > 0))
  {
    if (client.available())
    {
      int16_t v = client.read();
      *buffer++ = uint8_t(v);
      remain--;
    }
    else delay(1);
    if (millis() - start > 2000) break; // don't hang forever
  }
  return bytes - remain;
}

uint16_t read16LEFromClient(WiFiClient& client)
{
  // BMP data is stored little-endian, same as Arduino.
  uint16_t result;
  ((uint8_t *)&result)[0] = client.read(); // LSB
  ((uint8_t *)&result)[1] = client.read(); // MSB
  return result;
}

uint32_t read32LEFromClient(WiFiClient& client)
{
  // BMP data is stored little-endian, same as Arduino.
  uint32_t result;
  ((uint8_t *)&result)[0] = client.read(); // LSB
  ((uint8_t *)&result)[1] = client.read();
  ((uint8_t *)&result)[2] = client.read();
  ((uint8_t *)&result)[3] = client.read(); // MSB
  return result;
}

/**
 * @brief Skips "bytes" bytes in the client connection. Returns number of bytes read
 */
uint32_t skipBytes(WiFiClient& client, int32_t bytes)
{
  int32_t remain = bytes;
  uint32_t start = millis();
  while ((client.connected() || client.available()) && (remain > 0))
  {
    if (client.available())
    {
      client.read();
      remain--;
    }
    else delay(1);
    if (millis() - start > 2000) break; // don't hang forever
  }
  return bytes - remain;
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
  int16_t tbx, tby; uint16_t tbw, tbh; 
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

void clearScreenPowerOff(void) {
  display.firstPage();
  do
  {
    display.fillScreen(GxEPD_WHITE);
  }
  while (display.nextPage());

  display.powerOff();
}

bool connectAndGetImage(void)
{
  uint32_t startTime = millis();
  if (!client.connected()) {
    // Not connected, attempt reconnect
    if (!client.connect(hostname, httpsPort)) {
      Serial.println("ERROR: Failed to connect!");
      return false;
    }
  }

  Serial.print("INFO: Requesting URL: ");
  Serial.println(String("https://") + hostname + path);
  client.print(String("GET ") + path + " HTTP/1.1\r\n" +
               "Host: " + hostname + "\r\n" +
               "User-Agent: BusDepartureBoard\r\n" +
               "Connection: close\r\n\r\n");
  Serial.println("INFO: Request sent");

  bool connection_ok = false;

  Serial.println("<---- BEGIN HEADER ---->");
  while (client.connected())
  {
    String line = client.readStringUntil('\n');

    connection_ok = line.startsWith("HTTP/1.1 200 OK");
    Serial.println(line);

    if (line == "\r")
    {
      Serial.println("Headers received");
      break;
    }
  }
  Serial.println("<---- END HEADER ---->");

  uint16_t file_signature = 0;

  // Read bytes until we see the bitmap file header
  for (int16_t i = 0; i < MAX_EXPECTED_HEADER_POSITION_BYTES; i++)
  {
    if (!client.available()) {
      delay(100);
    } else {
      file_signature = read16LEFromClient(client);
    }
    Serial.println(file_signature);
    if (file_signature == BITMAP_SIGNATURE) break;
  }

  if(file_signature != BITMAP_SIGNATURE) {
    Serial.println("ERROR: Invalid file signature, did not match expected bitmap header");
    return false;
  }else{
    DEBUG_PRINTLN("Found signature!");
  }

  uint32_t fileSize = read32LEFromClient(client);
  uint32_t creatorBytes = read32LEFromClient(client); (void)creatorBytes; //unused
  uint32_t imageOffset = read32LEFromClient(client); // Start of image data
  uint32_t headerSize = read32LEFromClient(client);
  uint32_t width  = read32LEFromClient(client);
  int32_t height = (int32_t) read32LEFromClient(client);
  uint16_t planes = read16LEFromClient(client);
  uint16_t depth = read16LEFromClient(client); // bits per pixel
  uint32_t format = read32LEFromClient(client);
  uint32_t bytes_read = 7 * 4 + 3 * 2;
  uint8_t input_buffer[3 * 800]; // up to depth 24
  uint8_t output_row_mono_buffer[1872 / 8]; // buffer for at least one row of b/w bits
  uint8_t output_row_color_buffer[1872 / 8]; // buffer for at least one row of color bits
  uint8_t mono_palette_buffer[256 / 8]; // palette buffer for depth <= 8 b/w
  uint8_t color_palette_buffer[256 / 8]; // palette buffer for depth <= 8 c/w

#ifdef DEBUG_MODE
  Serial.print("File size: "); Serial.println(fileSize);
  Serial.print("Image Offset: "); Serial.println(imageOffset);
  Serial.print("Header size: "); Serial.println(headerSize);
  Serial.print("Bit Depth: "); Serial.println(depth);
  Serial.print("Image size: ");
  Serial.print(width);
  Serial.print('x');
  Serial.println(height);
  Serial.print("Planes: "); Serial.println(planes);
  Serial.print("format: "); Serial.println(format);
#endif

  if ((planes != 1) || ((format != 0) && (format != 3))) // uncompressed is handled, 565 also
  {
    Serial.println("ERROR: Unable to handle bitmap format!");
    return false;
  }

  uint32_t rowSize = (width * depth / 8 + 3) & ~3;

  connection_ok = true;
  uint8_t bitmask = 0xFF;
  uint8_t bitshift = 8 - depth;
  uint16_t red, green, blue;
  bool whitish = false;
  bool colored = false;
  if (depth <= 8)
  {
    bytes_read += skipBytes(client, imageOffset - (4 << depth) - bytes_read); // 54 for regular, diff for colorsimportant
    for (uint16_t pn = 0; pn < (1 << depth); pn++)
    {
      blue  = client.read();
      green = client.read();
      red   = client.read();
      client.read();
      bytes_read += 4;
      whitish = false ? ((red > 0x80) && (green > 0x80) && (blue > 0x80)) : ((red + green + blue) > 3 * 0x80); // whitish
      colored = (red > 0xF0) || ((green > 0xF0) && (blue > 0xF0)); // reddish or yellowish?
      if (0 == pn % 8) mono_palette_buffer[pn / 8] = 0;
      mono_palette_buffer[pn / 8] |= whitish << pn % 8;
      if (0 == pn % 8) color_palette_buffer[pn / 8] = 0;
      color_palette_buffer[pn / 8] |= colored << pn % 8;
    }
  }
  Serial.print("Whitish, colored: ");
  Serial.print(whitish);
  Serial.println(colored);

  display.clearScreen();
  display.setRotation(0);
  uint32_t rowPosition = true ? imageOffset + (0) * rowSize : imageOffset;
  bytes_read += skipBytes(client, rowPosition - bytes_read);

  Serial.println("About to for(each line)");
  for (uint16_t row = 0; row < height; row++, rowPosition += rowSize) // for each line
  {
    if (!(client.connected() || client.available())) break;
    delay(1); // yield() to avoid WDT
    uint32_t in_remain = rowSize;
    uint32_t in_idx = 0;
    uint32_t in_bytes = 0;
    uint8_t in_byte = 0; // for depth <= 8
    uint8_t in_bits = 0; // for depth <= 8
    uint8_t out_byte = 0xFF; // white (for w%8!=0 border)
    uint8_t out_color_byte = 0xFF; // white (for w%8!=0 border)
    uint32_t out_idx = 0;
    for (uint16_t col = 0; col < width; col++) // for each pixel
    {
      yield();
      if (!(client.connected() || client.available())) break;
      // Time to read more pixel data?
      if (in_idx >= in_bytes) // ok, exact match for 24bit also (size IS multiple of 3)
      {
        uint32_t get = in_remain > sizeof(input_buffer) ? sizeof(input_buffer) : in_remain;
        uint32_t got = read8n(client, input_buffer, get);
        while ((got < get) && connection_ok)
        {
          //Serial.print("got "); Serial.print(got); Serial.print(" < "); Serial.print(get); Serial.print(" @ "); Serial.println(bytes_read);
          uint32_t gotmore = read8n(client, input_buffer + got, get - got);
          got += gotmore;
          connection_ok = gotmore > 0;
        }
        in_bytes = got;
        in_remain -= got;
        bytes_read += got;
        in_idx = 0;
      }
      if (!connection_ok)
      {
        Serial.print("Error: got no more after "); Serial.print(bytes_read); Serial.println(" bytes read!");
        break;
      }
      switch (depth)
      {
        case 8:
          {
            if (0 == in_bits)
            {
              in_byte = input_buffer[in_idx++];
              in_bits = 8;
            }
            uint16_t pn = in_byte & bitmask;
            whitish = mono_palette_buffer[pn / 8] & (0x1 << pn % 8);
            colored = color_palette_buffer[pn / 8] & (0x1 << pn % 8);
            in_byte <<= depth;
            in_bits -= depth;
          }
          break;
      }
      if (whitish)
      {
        // keep white
      }
      else if (colored && false)
      {
        out_color_byte &= ~(0x80 >> col % 8); // colored
      }
      else
      {
        out_byte &= ~(0x80 >> col % 8); // black
      }
      if ((7 == col % 8) || (col == width - 1)) // write that last byte! (for w%8!=0 border)
      {
        output_row_color_buffer[out_idx] = out_color_byte;
        output_row_mono_buffer[out_idx++] = out_byte;
        out_byte = 0xFF; // white (for w%8!=0 border)
        out_color_byte = 0xFF; // white (for w%8!=0 border)
      }
    } // end pixel
    int16_t yrow = 0 + (true ? height - row - 1 : row);
    display.writeImage(output_row_mono_buffer, output_row_color_buffer, 0, yrow, width, 1);
  } // end line
  Serial.print("downloaded in ");
  Serial.print(millis() - startTime);
  Serial.println(" ms");
  display.refresh();

  Serial.print("bytes read "); Serial.println(bytes_read);

  client.stop();
  return true;
}