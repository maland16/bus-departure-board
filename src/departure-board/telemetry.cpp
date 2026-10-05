
#include "telemetry.h"
#include "credentials.h"
#include "wifi-driver.h"
#include "battery-driver.h"
#include "nvm-driver.h"
#include <HTTPClient.h>
#include <esp_system.h>
#include <esp_timer.h>

// Escape characters that are special in line-protocol tag values
static String escapeTag(const String& in) {
  String out;
  out.reserve(in.length() + 4);
  for (size_t i = 0; i < in.length(); i++) {
    char c = in[i];
    if (c == ' ' || c == ',' || c == '=' || c == '\\') out += '\\';
    out += c;
  }
  return out;
}

// Non-finite floats would produce an invalid line, so swap in a sentinel
static float sane(float v) { return isfinite(v) ? v : -1.0f; }

bool sendTelemetry() {
  if (WiFi.status() != WL_CONNECTED) return false;

  float solarV   = sane(getSolarVoltage());
  float batteryV = sane(getBatteryVoltage());
  int   mode     = (int)getLastDisplayedMode();
  long long uptimeS = esp_timer_get_time() / 1000000LL;  // 64-bit, no 49-day wrap

  String ssid = escapeTag(WiFi.SSID());
  if (ssid.length() == 0) ssid = "unknown";

  char body[512];
  int n = snprintf(body, sizeof(body),
    "diagnostics,device=%s "
      "solar_v=%.3f,battery_v=%.3f,rssi=%di,display_mode=%di,reset_reason=%di,"
      "heap_free=%ui,heap_min=%ui,uptime_s=%lldi\n"
    "network,device=%s,ssid=%s connected=1i",
    GRAFANA_DEVICE,
    solarV, batteryV, WiFi.RSSI(), mode, (int)esp_reset_reason(),
    (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(), uptimeS,
    GRAFANA_DEVICE, ssid.c_str());

  if (n < 0 || n >= (int)sizeof(body)) {
    Serial.println("Telemetry body truncated");
    return false;
  }

  WiFiClientSecure client;
  client.setInsecure();  // same trade-off as discussed earlier
  HTTPClient http;
  http.setTimeout(5000);
  http.begin(client, INFLUX_DB_URL);
  http.setAuthorization(GC_USER, GC_TOKEN);
  http.addHeader("Content-Type", "text/plain");
  int code = http.POST((uint8_t*)body, n);
  http.end();

  bool ok = (code >= 200 && code < 300);
  if (!ok) Serial.printf("Telemetry failed: %d\n", code);
  return ok;
}