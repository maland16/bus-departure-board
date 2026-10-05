#ifndef _CREDENTIALS_H_
#define _CREDENTIALS_H_

#define SERIAL_NUMBER "001"
#define CONFIGURED_STOP_ID "1117"

#define PRIMARY_WIFI_SSID ("your_ssid_here")
#define PRIMARY_WIFI_PASSWORD ("your_password_here")
#define SECONDARY_WIFI_SSID ("your_secondary_ssid_here")
#define SECONDARY_WIFI_PASSWORD ("your_secondary_password_here")
#define TERTIARY_WIFI_SSID ("your_tertiary_ssid_here")
#define TERTIARY_WIFI_PASSWORD ("your_tertiary_password_here")

#define INFLUX_DB_URL "https://prometheus-prod-56-prod-us-east-2.grafana.net/api/v1/push/influx/write"
#define GC_USER "your_grafana_user_id"
#define GC_TOKEN "your_grafana_token"
inline constexpr char GRAFANA_DEVICE[] = "departure-board-" SERIAL_NUMBER;

// Fetched with openssl s_client -showcerts -connect transit.ucop.me:443
// Certificate chain #1, valid until Mar 12 23:59:59 2027 GMT
const char web_cert [] PROGMEM = R"CERT(
-----BEGIN CERTIFICATE-----
replace with your transit certificate PEM contents
-----END CERTIFICATE-----
)CERT";

#endif // _CREDENTIALS_H_