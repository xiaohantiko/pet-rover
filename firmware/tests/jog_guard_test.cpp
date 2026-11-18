#include "jog_guard.hpp"
#include <assert.h>
#include <initializer_list>
#include <stdio.h>

JogGuard ready(uint32_t now) {
  JogGuard guard;
  guard.observe(now, true, false);
  assert(guard.command("ARM", now, true, true));
  return guard;
}

int main() {
  JogGuard cold;
  assert(cold.output() == JogGuard::idle && !cold.command("ARM", 0, true, true));
  for (const char* move : {"L+", "L-", "R+", "R-"}) {
    JogGuard guard = ready(100);
    assert(guard.command(move, 101, true, true) && guard.output() != JogGuard::idle);
    guard.observe(240, true, true);
    assert(guard.output() != JogGuard::idle);
    guard.observe(241, true, true);
    assert(guard.output() == JogGuard::idle && !guard.command("ARM", 242, true, true));
  }
  JogGuard wrap = ready(UINT32_MAX-50);
  assert(wrap.command("L+", UINT32_MAX-49, true, true));
  wrap.observe(89, true, true);
  assert(wrap.output() != JogGuard::idle);
  wrap.observe(90, true, true);
  assert(wrap.output() == JogGuard::idle);
  for (int event = 0; event < 5; ++event) {
    JogGuard guard = ready(0);
    assert(guard.command("R-", 1, true, true));
    if (event == 0) guard.observe(2, false, true);
    if (event == 1) guard.observe(2, true, false);
    if (event == 2) assert(guard.command("STOP", 2, true, true));
    if (event == 3) assert(!guard.command("L+", 2, true, true));
    if (event == 4) assert(!guard.command("nonsense", 2, true, true));
    assert(guard.output() == JogGuard::idle && !guard.command("R-", 3, true, true));
  }
  JogGuard expired = ready(3);
  assert(!expired.command("L+", 1003, true, true));
  puts("PASS: cold start; four jog directions; exact timeout; no auto-rearm; clock rollover; interlock; deadman; STOP; duplicate and malformed commands; arm-window expiry.");
}
