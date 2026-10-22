#ifndef WIFI_DRIVER_H
#define WIFI_DRIVER_H

#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiClientSecure.h>
#include <esp_attr.h>

extern WiFiMulti wifiMulti;
extern WiFiClientSecure client;
extern uint8_t consecutiveWifiFailures;
extern uint8_t wifiFailuresBeforeConnect;

// Public API
void initWifi();
void refreshWifiConnection();
bool isWifiConnected();

#endif // WIFI_DRIVER_H