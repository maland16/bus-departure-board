#include "wifi-driver.h"
#include "credentials.h"
#include "debug-print.h"
#include "rtc-driver.h"

WiFiClientSecure client;
WiFiMulti wifiMulti;

void initWifiClient()
{
  if (web_cert) client.setCACert(web_cert);
}

void initWifi()
{
  wifiMulti.addAP(PRIMARY_WIFI_SSID, PRIMARY_WIFI_PASSWORD);
  wifiMulti.addAP("ssid_from_AP_2", "your_password_for_AP_2");

  DEBUG_PRINTLN("Initializing Wifi...");

  refreshWifiConnection();
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