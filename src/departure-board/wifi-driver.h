#ifndef WIFI_DRIVER_H
#define WIFI_DRIVER_H

#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiClientSecure.h>

extern WiFiMulti wifiMulti;
extern WiFiClientSecure client;

// Public API
void initWifiClient();
void initWifi();
void refreshWifiConnection();

#endif // WIFI_DRIVER_H