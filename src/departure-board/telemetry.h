#ifndef TELEMETRY_H
#define TELEMETRY_H

#include <Arduino.h>

bool sendTelemetry(void);
void sendTelemetryWithRetries(void);

#endif // TELEMETRY_H
