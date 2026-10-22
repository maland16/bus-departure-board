# E-Ink Display

The board uses a Good Display **GDEM075T41WT**, a 7.5" black/white, wide-temperature e-paper panel.

Product page: https://www.good-display.com/product/700.html

## Specs

| | |
| - | - |
| Model | GDEM075T41WT (Good Display serial 100750T4MW) |
| Size | 7.5" |
| Colors | Black / white |
| Resolution | 800 x 480 (124 DPI) |
| Driver IC | UC8179 |
| Interface | SPI, 3.3V supply and logic |
| Operating temperature | -25°C to 50°C (the product page also lists -20°C to 50°C in one place) |
| Full refresh time | 4 s, tested at 25°C ambient |
| Partial / fast refresh | Not listed on the product page |

The product page also notes that dense graphics can look grainy at 30-50°C, and that the screen flickers several times during a full refresh. Both are normal for this panel.

## Firmware

- **Driver class:** there's no GxEPD2 driver class specifically for this panel, so the firmware uses `GxEPD2_750_GDEY075T7` (also 800x480 with a UC8179). Full refreshes use the waveform stored in the panel itself (OTP), so they get this panel's own wide-temperature timing. That's why a full refresh takes about 4 s, not the 1.2 s GxEPD2 lists for the GDEY075T7.
- **Partial refresh is untested on this panel.** GxEPD2's fast partial mode for the GDEY075T7 forces the controller's temperature setting to 110 to select a fast waveform from OTP. It's unknown whether this panel's OTP has one, and forcing the temperature turns off temperature compensation, which matters in the cold. Bench test it (including near freezing) before relying on it.
- **Refresh timing:** GxEPD2 prints how long each refresh takes to serial, e.g. `_Update_Full : 4xxxxxx` (in microseconds).
- **Image source:** the transit server renders the image for this panel at `/stops/<stop id>/display/gdem075t41wt/display.bmp`, as an 800 x 480 BMP.

## Wiring (Adafruit ESP32-S3 Feather)

From `src/departure-board/GxEPD2_display_selection_new_style.h`:

| Display | Feather pin |
| - | - |
| CS | GPIO 11 |
| DC | GPIO 10 |
| RST | GPIO 9 |
| BUSY | GPIO 6 |
| SCK | SCK (GPIO 36) |
| MOSI / DIN | MO (GPIO 35) |
