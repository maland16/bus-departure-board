#include "wifi-driver.h"
#include "credentials.h"
#include "rtc-driver.h"
#include "wake-stats.h"

#define WIFI_CLIENT_TIMEOUT_SEC (5)
// WiFiMulti's default is 5s, which a weak signal or slow DHCP can exceed
#define WIFI_CONNECT_TIMEOUT_MS (10000)

WiFiClientSecure client;
WiFiMulti wifiMulti;

// Saved in RTC memory so we can back off while wifi is down
RTC_DATA_ATTR uint8_t consecutiveWifiFailures = 0;
// How many wake-ups in a row failed to connect before this one, reported in
// telemetry so outages show up once we're back online
uint8_t wifiFailuresBeforeConnect = 0;

void initWifi()
{
  // Copilot came up with these next few lines, it looks a little weird 
  // but it solves the problem of not having to add a new line here every
  // time we want to add wifi credentials
  #define WIFI_CREDENTIALS_ENTRY(ssid, password) wifiMulti.addAP(ssid, password);
  WIFI_CREDENTIALS(WIFI_CREDENTIALS_ENTRY);
  #undef WIFI_CREDENTIALS_ENTRY

  Serial.println("Initializing Wifi...");

  const uint32_t connectStartMs = millis();
  refreshWifiConnection();
  wakeStats.wifiConnectMs = millis() - connectStartMs;

  if (isWifiConnected()) {
    wifiFailuresBeforeConnect = consecutiveWifiFailures;
    consecutiveWifiFailures = 0;
  } else if (consecutiveWifiFailures < UINT8_MAX) {
    consecutiveWifiFailures++;
  }

  client.setCACert(web_cert);

  client.setTimeout(WIFI_CLIENT_TIMEOUT_SEC);
}

bool isWifiConnected()
{
  return WiFi.status() == WL_CONNECTED;
}

void refreshWifiConnection()
{
  if (WiFi.status() != WL_CONNECTED){
    // .run() connects to the strongest wifi on the list
    if (wifiMulti.run(WIFI_CONNECT_TIMEOUT_MS) == WL_CONNECTED)
    {
      Serial.print("Wifi Status: ");
      Serial.println(WiFi.status());
      Serial.print("Connected to: ");
      Serial.println(WiFi.SSID());
      Serial.print("RSSI: ");
      Serial.println(WiFi.RSSI());
      Serial.print("IP address: ");
      Serial.println(WiFi.localIP());
    } else {
      Serial.println("Failed to connect to wifi!");
      Serial.print("Wifi Status: ");
      Serial.println(WiFi.status());
    }
  }
}