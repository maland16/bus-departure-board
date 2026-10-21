#include "wifi-driver.h"
#include "credentials.h"
#include "debug-print.h"
#include "rtc-driver.h"

#define WIFI_CLIENT_TIMEOUT_SEC (5)

WiFiClientSecure client;
WiFiMulti wifiMulti;

void initWifi()
{
  // Copilot came up with these next few lines, it looks a little weird 
  // but it solves the problem of not having to add a new line here every
  // time we want to add wifi credentials
  #define WIFI_CREDENTIALS_ENTRY(ssid, password) wifiMulti.addAP(ssid, password);
  WIFI_CREDENTIALS(WIFI_CREDENTIALS_ENTRY);
  #undef WIFI_CREDENTIALS_ENTRY

  DEBUG_PRINTLN("Initializing Wifi...");

  refreshWifiConnection();

  client.setCACert(web_cert);

  client.setTimeout(WIFI_CLIENT_TIMEOUT_SEC);
}

void refreshWifiConnection()
{
  if (WiFi.status() != WL_CONNECTED){
    // .run() connects to the strongest wifi on the list
    if (wifiMulti.run() == WL_CONNECTED)
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
      ERROR_PRINTLN("Failed to connect to wifi!");
      Serial.print("Wifi Status: ");
      Serial.println(WiFi.status());
    }
  }
}