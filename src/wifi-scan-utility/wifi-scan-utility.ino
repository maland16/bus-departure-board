#include <WiFi.h>

const uint8_t SCAN_ROUNDS = 5;

struct NetworkSample {
  String ssid;
  int32_t rssi;
};

static void printNetworkList(const std::vector<NetworkSample>& networks) {
  Serial1.println("Nearby WiFi networks:");
  if (networks.empty()) {
    Serial1.println("  none found");
    return;
  }

  for (size_t i = 0; i < networks.size(); ++i) {
    Serial1.printf("  %2zu. %-28s %4d dBm\n",
                  i + 1,
                  networks[i].ssid.c_str(),
                  networks[i].rssi);
  }
}

void setup() {
  Serial1.begin(115200, SERIAL_8N1, RX, TX);
  delay(1000);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);

  Serial1.println("Scanning for nearby WiFi networks...");

  std::vector<NetworkSample> averaged;
  std::vector<String> seenSsids;

  for (uint8_t round = 0; round < SCAN_ROUNDS; ++round) {
    Serial1.printf("Scan round %u/%u\n", round + 1, SCAN_ROUNDS);
    int numNetworks = WiFi.scanNetworks(true, false, false, 300); // async, no auth, 300 ms

    unsigned long start = millis();
    while (WiFi.scanComplete() == WIFI_SCAN_RUNNING && millis() - start < 5000) {
      delay(50);
    }

    int found = WiFi.scanComplete();
    if (found < 0) {
      Serial1.println("Scan failed or timed out.");
      continue;
    }

    for (int i = 0; i < found; ++i) {
      String ssid = WiFi.SSID(i);
      int32_t rssi = WiFi.RSSI(i);

      if (ssid.length() == 0) {
        continue;
      }

      bool exists = false;
      for (size_t j = 0; j < seenSsids.size(); ++j) {
        if (seenSsids[j].equals(ssid)) {
          exists = true;
          break;
        }
      }

      if (!exists) {
        seenSsids.push_back(ssid);
        averaged.push_back({ssid, rssi});
      } else {
        for (size_t j = 0; j < averaged.size(); ++j) {
          if (averaged[j].ssid.equals(ssid)) {
            averaged[j].rssi = ((averaged[j].rssi * round) + rssi) / (round + 1);
            break;
          }
        }
      }
    }

    WiFi.scanDelete();
    delay(200);
  }

  std::sort(averaged.begin(), averaged.end(),
            [](const NetworkSample& a, const NetworkSample& b) {
              return a.rssi > b.rssi;
            });

  printNetworkList(averaged);
  WiFi.mode(WIFI_OFF);
}

void loop() {
  // Intentionally empty.
}
