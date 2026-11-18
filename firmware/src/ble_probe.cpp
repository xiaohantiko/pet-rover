#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <initializer_list>

namespace {
constexpr uint8_t local_button = 11;
constexpr char service_id[] = "07040121-fd38-4c1b-a4b9-7e016c104008";
constexpr char status_id[] = "07040122-fd38-4c1b-a4b9-7e016c104008";
BLEAdvertising* advertising = nullptr;
bool advertising_requested = false;
}

void setup() {
  for (uint8_t pin : {4, 5, 6, 7}) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  pinMode(local_button, INPUT_PULLUP);
  BLEDevice::init("PetRover-Bench");
  BLEService* service = BLEDevice::createServer()->createService(service_id);
  service->createCharacteristic(status_id, BLECharacteristic::PROPERTY_READ)
      ->setValue("bench-v1;motion=disabled");
  service->start();
  advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(service_id);
  advertising->setScanResponse(true);
}

void loop() {
  const bool pressed = digitalRead(local_button) == LOW;
  if (pressed && !advertising_requested) advertising->start();
  if (!pressed && advertising_requested) advertising->stop();
  advertising_requested = pressed;
  delay(25);
}
