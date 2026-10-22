#include "wifi-driver.h"
#include "credentials.h"
#include "rtc-driver.h"
#include "wake-stats.h"

// Stream timeouts are in milliseconds (HTTPClient replaces this with its own once connected)
#define WIFI_CLIENT_TIMEOUT_MS (5000)
// The TLS handshake timeout defaults to 120s, longer than the 30s task watchdog,
// so a stalled handshake would reset the board instead of failing cleanly
#define TLS_HANDSHAKE_TIMEOUT_SEC (10)
// WiFiMulti's default is 5s, which a weak signal or slow DHCP can exceed
#define WIFI_CONNECT_TIMEOUT_MS (10000)
// Reconnecting to a known access point normally takes well under this
#define FAST_CONNECT_TIMEOUT_MS (3000)

WiFiClientSecure client;
WiFiMulti wifiMulti;

// Saved in RTC memory so we can back off while wifi is down
RTC_DATA_ATTR uint8_t consecutiveWifiFailures = 0;
// How many wake-ups in a row failed to connect before this one, reported in
// telemetry so outages show up once we're back online
uint8_t wifiFailuresBeforeConnect = 0;

typedef struct {
  const char *ssid;
  const char *password;
} WifiCredential;

// Copilot came up with this macro trick, it looks a little weird but it
// solves the problem of not having to add a new line here every time we want
// to add wifi credentials
#define WIFI_CREDENTIALS_ENTRY(ssid, password) { ssid, password },
static const WifiCredential wifiCredentials[] = { WIFI_CREDENTIALS(WIFI_CREDENTIALS_ENTRY) };
#undef WIFI_CREDENTIALS_ENTRY
static const int wifiCredentialCount = sizeof(wifiCredentials) / sizeof(wifiCredentials[0]);

// The access point we last connected to, saved so the next wake-up can connect
// to it directly instead of WiFiMulti scanning every channel first (~3s)
RTC_DATA_ATTR static int8_t lastApCredentialIndex = -1;
RTC_DATA_ATTR static uint8_t lastApBssid[6];
RTC_DATA_ATTR static int32_t lastApChannel = 0;

static bool connectToLastAccessPoint(void)
{
  if (lastApCredentialIndex < 0 || lastApCredentialIndex >= wifiCredentialCount || lastApChannel == 0) {
    return false;
  }

  const WifiCredential &credential = wifiCredentials[lastApCredentialIndex];
  WiFi.mode(WIFI_STA);
  WiFi.begin(credential.ssid, credential.password, lastApChannel, lastApBssid);

  const uint32_t startMs = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startMs < FAST_CONNECT_TIMEOUT_MS) {
    delay(10);
  }

  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  Serial.println("Couldn't reconnect to last access point, scanning instead");
  WiFi.disconnect();
  lastApCredentialIndex = -1;
  return false;
}

static void saveConnectedAccessPoint(void)
{
  lastApCredentialIndex = -1;
  for (int i = 0; i < wifiCredentialCount; i++) {
    if (WiFi.SSID() == wifiCredentials[i].ssid) {
      lastApCredentialIndex = i;
      break;
    }
  }

  const uint8_t *bssid = WiFi.BSSID();
  if (bssid != NULL) {
    memcpy(lastApBssid, bssid, sizeof(lastApBssid));
  }
  lastApChannel = WiFi.channel();
}

void initWifi()
{
  // Don't write the wifi config to flash on every wake-up
  WiFi.persistent(false);

  for (int i = 0; i < wifiCredentialCount; i++) {
    wifiMulti.addAP(wifiCredentials[i].ssid, wifiCredentials[i].password);
  }

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

  client.setTimeout(WIFI_CLIENT_TIMEOUT_MS);
  client.setHandshakeTimeout(TLS_HANDSHAKE_TIMEOUT_SEC);
}

bool isWifiConnected()
{
  return WiFi.status() == WL_CONNECTED;
}

void refreshWifiConnection()
{
  if (WiFi.status() != WL_CONNECTED){
    if (connectToLastAccessPoint()) {
      Serial.println("Reconnected to last access point");
    }

    // .run() connects to the strongest wifi on the list
    if (WiFi.status() == WL_CONNECTED || wifiMulti.run(WIFI_CONNECT_TIMEOUT_MS) == WL_CONNECTED)
    {
      saveConnectedAccessPoint();

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