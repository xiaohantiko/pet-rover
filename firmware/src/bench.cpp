#include <Arduino.h>
#include <initializer_list>
#include "jog_guard.hpp"

#ifndef RAISED_WHEEL_JOG
#define RAISED_WHEEL_JOG 0
#endif

namespace {
constexpr uint8_t left_pwm = 4, left_dir = 5, right_pwm = 6, right_dir = 7;
constexpr uint8_t interlock = 10, deadman = 11;
constexpr uint8_t left_a = 15, left_b = 16, right_a = 17, right_b = 18;
constexpr uint8_t duty = RAISED_WHEEL_JOG ? 36 : 0;
volatile uint32_t left_edges = 0, right_edges = 0;
JogGuard guard;
char line[12];
size_t used = 0;
bool discard = false;
JogGuard::Output previous = JogGuard::idle;

void IRAM_ATTR count_left() { left_edges += digitalRead(left_b) ? 1u : UINT32_MAX; }
void IRAM_ATTR count_right() { right_edges += digitalRead(right_b) ? 1u : UINT32_MAX; }
bool closed() { return digitalRead(interlock) == LOW; }
bool held() { return digitalRead(deadman) == LOW; }

void outputs() {
  const auto next = guard.output();
  if (next == previous) return;
  ledcWrite(0, 0);
  ledcWrite(1, 0);
  digitalWrite(left_dir, next == JogGuard::left_reverse);
  digitalWrite(right_dir, next == JogGuard::right_reverse);
  if (next == JogGuard::left_forward || next == JogGuard::left_reverse) ledcWrite(0, duty);
  if (next == JogGuard::right_forward || next == JogGuard::right_reverse) ledcWrite(1, duty);
  previous = next;
}

void handle() {
  if (!strcmp(line, "STATUS")) {
    if (Serial.availableForWrite() >= 64)
      Serial.printf("jog=%d nc=%d hold=%d arm=%d out=%d enc=%ld,%ld\n", RAISED_WHEEL_JOG,
                    closed(), held(), guard.armed(), int(guard.output()),
                    long(int32_t(left_edges)), long(int32_t(right_edges)));
    return;
  }
  const bool okay = guard.command(line, millis(), closed(), held());
  outputs();
  if (Serial.availableForWrite() >= 8) Serial.println(okay ? "ACK" : "BLOCK");
}
}

void setup() {
  for (uint8_t pin : {left_pwm, left_dir, right_pwm, right_dir}) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  for (uint8_t pin : {interlock, deadman}) pinMode(pin, INPUT_PULLUP);
  for (uint8_t pin : {left_a, left_b, right_a, right_b}) pinMode(pin, INPUT);
  ledcSetup(0, 16000, 8);
  ledcSetup(1, 16000, 8);
  ledcWrite(0, 0);
  ledcWrite(1, 0);
  ledcAttachPin(left_pwm, 0);
  ledcAttachPin(right_pwm, 1);
  attachInterrupt(left_a, count_left, RISING);
  attachInterrupt(right_a, count_right, RISING);
  Serial.begin(115200);
}

void loop() {
  guard.observe(millis(), closed(), held());
  outputs();
  for (uint8_t count = 0; count < 32 && Serial.available(); ++count) {
    const int value = Serial.read();
    if (value == '\n') {
      line[used] = '\0';
      if (used && !discard) handle();
      used = 0;
      discard = false;
    } else if (value == '\r') {
      continue;
    } else if (discard || value < 32 || value > 126 || used >= sizeof(line)-1) {
      discard = true;
      guard.stop();
      outputs();
    } else {
      line[used++] = char(value);
    }
  }
  delay(1);
}
