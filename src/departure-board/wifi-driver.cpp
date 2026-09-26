#include "wifi-driver.h"
#include "credentials.h"
#include "debug-print.h"
#include "rtc-driver.h"

#define WIFI_CLIENT_TIMEOUT_SEC (5)

WiFiClientSecure client;
WiFiMulti wifiMulti;

void initWifi()
{
  wifiMulti.addAP(PRIMARY_WIFI_SSID, PRIMARY_WIFI_PASSWORD);
  wifiMulti.addAP(SECONDARY_WIFI_SSID, SECONDARY_WIFI_PASSWORD);
  wifiMulti.addAP(TERTIARY_WIFI_SSID, TERTIARY_WIFI_PASSWORD);

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