#include <Arduino.h>
#include <Wire.h>
#include <VL53L1X.h>
#include <initializer_list>

namespace {
constexpr uint8_t mux = 0x70;
constexpr uint8_t channels = 7;
VL53L1X sensors[channels];
bool online[channels]{};
bool select_channel(uint8_t channel) {
  Wire.beginTransmission(mux);
  Wire.write(uint8_t(1u << channel));
  return Wire.endTransmission() == 0;
}
}

void setup() {
  for (uint8_t pin : {4, 5, 6, 7}) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  Serial.begin(115200);
  Wire.begin(8, 9, 100000);
  Wire.setTimeOut(20);
  for (uint8_t channel = 0; channel < channels; ++channel) {
    sensors[channel].setTimeout(100);
    if (!select_channel(channel) || !sensors[channel].init()) continue;
    if (!sensors[channel].setDistanceMode(VL53L1X::Short) ||
        !sensors[channel].setMeasurementTimingBudget(33000)) continue;
    sensors[channel].startContinuous(60);
    online[channel] = true;
  }
  Serial.println("ms,channel,range_mm,status");
}

void loop() {
  for (uint8_t channel = 0; channel < channels; ++channel) {
    Serial.printf("%lu,%u,", millis(), channel);
    if (!online[channel] || !select_channel(channel)) {
      Serial.println(",OFFLINE");
      continue;
    }
    if (!sensors[channel].dataReady()) {
      Serial.println(",NOT_READY");
      continue;
    }
    sensors[channel].read(false);
    const auto &range = sensors[channel].ranging_data;
    if (sensors[channel].last_status || range.range_status != VL53L1X::RangeValid) {
      Serial.printf(",INVALID_%u\n", unsigned(range.range_status));
      continue;
    }
    Serial.printf("%u,OK\n", range.range_mm);
  }
  delay(100);
}
