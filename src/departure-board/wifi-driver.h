#ifndef WIFI_DRIVER_H
#define WIFI_DRIVER_H

#include <WiFi.h>
#include <WiFiClientSecure.h>

extern WiFiClientSecure client;
extern uint8_t consecutiveWifiFailures;
extern uint8_t wifiFailuresBeforeConnect;

// Public API
void initWifi();
bool isWifiConnected();

#endif // WIFI_DRIVER_H