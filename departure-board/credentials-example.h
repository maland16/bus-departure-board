#ifndef _CREDENTIALS_H_
#define _CREDENTIALS_H_

#define PRIMARY_WIFI_SSID ("your_ssid_here")
#define PRIMARY_WIFI_PASSWORD ("your_password_here") 

// Fetched with openssl s_client -showcerts -connect devinmalanaphy.com:443
// Certificate chain #1, valid until Mar 12 23:59:59 2027 GMT
const char web_cert [] PROGMEM = R"CERT(
-----BEGIN CERTIFICATE-----
add your certificate data here
-----END CERTIFICATE-----
)CERT";

#endif // _CREDENTIALS_H_