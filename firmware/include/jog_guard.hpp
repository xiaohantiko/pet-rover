#pragma once
#include <stdint.h>
#include <string.h>

class JogGuard {
 public:
  enum Output { idle, left_forward, left_reverse, right_forward, right_reverse };
  static constexpr uint32_t pulse_ms = 140;
  static constexpr uint32_t arm_window_ms = 1000;

  void observe(uint32_t now, bool loop_closed, bool held) {
    if (!loop_closed) return stop();
    if (!held) {
      stop();
      released_ = true;
      return;
    }
    if ((active_ != idle && uint32_t(now - started_) >= pulse_ms) ||
        (armed_ && uint32_t(now - armed_at_) >= arm_window_ms)) stop();
  }

  bool command(const char* cmd, uint32_t now, bool loop_closed, bool held) {
    observe(now, loop_closed, held);
    if (strcmp(cmd, "STOP") == 0) { stop(); return true; }
    if (!loop_closed || !held) { stop(); return false; }
    if (strcmp(cmd, "ARM") == 0 && released_ && active_ == idle) {
      armed_at_ = now;
      released_ = false;
      armed_ = true;
      return true;
    }
    if (!armed_) { stop(); return false; }
    Output next = idle;
    if (strcmp(cmd, "L+") == 0) next = left_forward;
    if (strcmp(cmd, "L-") == 0) next = left_reverse;
    if (strcmp(cmd, "R+") == 0) next = right_forward;
    if (strcmp(cmd, "R-") == 0) next = right_reverse;
    if (next == idle) { stop(); return false; }
    active_ = next;
    armed_ = false;
    started_ = now;
    return true;
  }

  void stop() { active_ = idle; armed_ = false; released_ = false; }
  Output output() const { return active_; }
  bool armed() const { return armed_; }

 private:
  Output active_ = idle;
  bool armed_ = false;
  bool released_ = false;
  uint32_t started_ = 0;
  uint32_t armed_at_ = 0;
};
