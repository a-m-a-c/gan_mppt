#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "channel.h"
#include "system.h"
#include "pwm.h"
#include "command.h"
#include "transport.h"
#include "mode_manual_duty.h"

channel_t channel_a, channel_b, channel_c, channel_d, channel_e;
system_t sys;
bool mode_pi_test_set_target(uint16_t mv) { (void)mv; return false; }
static uint32_t now, last_write;
static bool fault, bypass, reject_step, reject_start;
static unsigned starts;
static unsigned fault_poll_count, fault_on_poll, fault_kind;
static transport_frame_t frames[4];
static unsigned count, cursor;

uint32_t HAL_GetTick(void) { return now; }
bool pwm_faults_present(void) {
  fault_poll_count++;
  if (fault_on_poll && fault_poll_count == fault_on_poll) {
    fault = true;
    channel_a.pwm.op_state = PWM_STATE_FAULTED;
    if (fault_kind == 1) channel_a.pwm.ocp_latched = true;
    if (fault_kind == 2) sys.ovp_latched = true;
    return false; // IRQ just after the caller's final fault read.
  }
  return fault;
}
void enable_bypass(bool value) { bypass = value; }
void pwm_stop_all(void) {
  channel_a.pwm.op_state = channel_b.pwm.op_state = channel_c.pwm.op_state = PWM_STATE_STOPPED;
  channel_d.pwm.op_state = channel_e.pwm.op_state = PWM_STATE_STOPPED;
  channel_a.pwm.duty_applied = 0;
}
bool pwm_start(uint32_t ch, uint16_t duty) {
  assert(ch == CHANNEL_A && duty == 0);
  if (reject_start) return false;
  starts++;
  channel_a.pwm.op_state = PWM_STATE_RUNNING;
  channel_a.pwm.duty_applied = duty;
  last_write = now;
  return true;
}
bool pwm_set_duty_cycle(uint32_t ch, uint16_t duty) {
  assert(ch == CHANNEL_A && duty <= 850);
  assert(now - last_write >= 100);
  assert(duty == channel_a.pwm.duty_applied + 1 || duty + 1 == channel_a.pwm.duty_applied);
  if (reject_step) return false;
  channel_a.pwm.duty_applied = duty;
  last_write = now;
  return true;
}
bool serial_take_next_frame(transport_frame_t *frame) {
  if (cursor == count) return false;
  *frame = frames[cursor++];
  return true;
}
static void frame(uint8_t op, uint8_t len, uint16_t value) {
  assert(count < 4);
  frames[count++] = (transport_frame_t){.op = op, .len = len,
    .data = {(uint8_t)value, (uint8_t)(value >> 8)}};
}
static void service_commands(void) {
  command_service();
  count = cursor = 0;
}
static void setup(uint32_t tick) {
  mode_manual_duty_end();
  memset(&channel_a, 0, sizeof(channel_a));
  now = tick;
  fault = reject_step = reject_start = false;
  bypass = true;
  starts = count = cursor = 0;
  fault_poll_count = fault_on_poll = fault_kind = 0;
  command_init();
  sys.state = SYSTEM_STATE_ACTIVE;
  sys.ovp_latched = false;
  sys.mode = MODE_MANUAL_DUTY;
  sys.vbus_mv = 47000;
  channel_a.telem = (chan_telem_t){.vin_v = 29, .vout_v = 29, .valid = true, .tick_ms = now};
}
static mode_state_t step(uint32_t ms) {
  now += ms;
  channel_a.telem.tick_ms = now;
  return mode_manual_duty_service(false);
}
static void stopped(void) {
  assert(channel_a.pwm.op_state == PWM_STATE_STOPPED);
  assert(channel_b.pwm.op_state == PWM_STATE_STOPPED && channel_c.pwm.op_state == PWM_STATE_STOPPED);
  assert(channel_d.pwm.op_state == PWM_STATE_STOPPED && channel_e.pwm.op_state == PWM_STATE_STOPPED);
  assert(!bypass && mode_manual_duty_target() == UINT16_MAX);
  assert(!mode_manual_duty_set(400));
}

int main(void) {
  for (unsigned wrap = 0; wrap < 2; wrap++) {
    setup(wrap ? UINT32_MAX - 50U : 0);
    assert(!mode_manual_duty_set(500));
    assert(mode_manual_duty_begin() == MODE_INIT_OK);
    assert(starts == 1 && mode_manual_duty_target() == 0 && !bypass);
    assert(mode_manual_duty_set(850));
    assert(!mode_manual_duty_set(851) && !mode_manual_duty_set(1000) && !mode_manual_duty_set(UINT16_MAX));
    assert(mode_manual_duty_target() == 850);
    assert(step(99) == MODE_STATE_RUNNING && channel_a.pwm.duty_applied == 0);
    assert(step(1) == MODE_STATE_RUNNING && channel_a.pwm.duty_applied == 1);
    assert(step(1000) == MODE_STATE_RUNNING && channel_a.pwm.duty_applied == 2);
    assert(step(0) == MODE_STATE_RUNNING && channel_a.pwm.duty_applied == 2);
    for (unsigned i = 2; i < 850; i++) assert(step(100) == MODE_STATE_RUNNING);
    assert(channel_a.pwm.duty_applied == 850);
    assert(step(100) == MODE_STATE_RUNNING && channel_a.pwm.duty_applied == 850);
    assert(mode_manual_duty_set(0));
    for (unsigned i = 0; i < 850; i++) assert(step(100) == MODE_STATE_RUNNING);
    assert(channel_a.pwm.duty_applied == 0);
    assert(mode_manual_duty_service(true) == MODE_STATE_EXIT);
    stopped();
    assert(mode_manual_duty_begin() == MODE_INIT_OK && mode_manual_duty_target() == 0);
  }
  const manual_fault_code_t expected[] = {
    MANUAL_FAULT_INPUT_CURRENT, MANUAL_FAULT_OUTPUT_CURRENT, MANUAL_FAULT_INPUT_REVERSE,
    MANUAL_FAULT_OUTPUT_REVERSE, MANUAL_FAULT_BUS_HIGH, MANUAL_FAULT_BUS_LOW,
    MANUAL_FAULT_INPUT_LOW, MANUAL_FAULT_INPUT_HIGH, MANUAL_FAULT_OUTPUT_HEADROOM,
    MANUAL_FAULT_TELEM_NONFINITE, MANUAL_FAULT_TELEM_INVALID, MANUAL_FAULT_TELEM_STALE,
    MANUAL_FAULT_HARDWARE_OTHER, MANUAL_FAULT_PWM_WRITE, MANUAL_FAULT_PWM_DUTY, MANUAL_FAULT_PWM_STATE
  };
  for (unsigned test = 0; test < 16; test++) {
    setup(0);
    assert(mode_manual_duty_begin() == MODE_INIT_OK);
    assert(mode_manual_duty_set(500));
    assert(step(100) == MODE_STATE_RUNNING);
    switch (test) {
      case 0: channel_a.telem.iin_a = 9.5f; break;
      case 1: channel_a.telem.iout_a = 8.0f; break;
      case 2: channel_a.telem.iin_a = -10.0f; break;
      case 3: channel_a.telem.iout_a = -10.0f; break;
      case 4: sys.vbus_mv = 50000; break;
      case 5: sys.vbus_mv = 34999; break;
      case 6: channel_a.telem.vin_v = 9.99f; break;
      case 7: channel_a.telem.vin_v = 35; break;
      case 8: channel_a.telem.vout_v = 48; break;
      case 9: channel_a.telem.iin_a = NAN; break;
      case 10: channel_a.telem.valid = false; break;
      case 11: now += 121; break;
      case 12: fault = true; break;
      case 13: reject_step = true; now += 100; channel_a.telem.tick_ms = now; break;
      case 14: channel_a.pwm.duty_applied = 700; break;
      case 15: channel_a.pwm.op_state = PWM_STATE_STOPPED; break;
    }
    assert(mode_manual_duty_service(false) == MODE_STATE_FAULTED);
    assert(mode_manual_duty_fault()->code == expected[test]);
    assert(mode_manual_duty_fault()->duty == 1);
    if (test == 0) assert(mode_manual_duty_fault()->value == 9500);
    if (test == 2) assert(mode_manual_duty_fault()->value == -10000);
    stopped();
    const manual_fault_t saved = *mode_manual_duty_fault();
    mode_manual_duty_end();
    assert(memcmp(&saved, mode_manual_duty_fault(), sizeof(saved)) == 0);
  }
  // Reproduce app_loop's hardware-fault path: stop/reset the driver first,
  // then end the mode. The saved duty must survive and identify channel A OCP.
  setup(0);
  assert(mode_manual_duty_begin() == MODE_INIT_OK && mode_manual_duty_set(620));
  for (unsigned i = 0; i < 615; i++) assert(step(100) == MODE_STATE_RUNNING);
  fault = true; channel_a.pwm.ocp_latched = true;
  pwm_stop_all();
  mode_manual_duty_end();
  assert(mode_manual_duty_fault()->code == MANUAL_FAULT_HARDWARE_OCP_A);
  assert(mode_manual_duty_fault()->duty == 615);
  stopped();
  // Inject an IRQ after the main-loop hardware check, at the same duty as
  // capture 20261030-151327. Report its cause rather than state value 3.
  for (unsigned kind = 1; kind <= 3; kind++) {
    for (unsigned at_poll = 1; at_poll <= 2; at_poll++) {
      setup(0);
      assert(mode_manual_duty_begin() == MODE_INIT_OK && mode_manual_duty_set(654));
      for (unsigned i = 0; i < 654; i++) assert(step(100) == MODE_STATE_RUNNING);
      if (at_poll == 2) channel_a.pwm.op_state = PWM_STATE_FAULTED;
      fault_poll_count = 0; fault_on_poll = at_poll; fault_kind = kind;
      assert(mode_manual_duty_service(false) == MODE_STATE_FAULTED);
      assert(mode_manual_duty_fault()->code == (kind == 1 ? MANUAL_FAULT_HARDWARE_OCP_A :
        kind == 2 ? MANUAL_FAULT_HARDWARE_OVP : MANUAL_FAULT_HARDWARE_OTHER));
      assert(mode_manual_duty_fault()->duty == 654);
      stopped();
    }
  }
  setup(0);
  assert(mode_manual_duty_begin() == MODE_INIT_OK);
  assert(mode_manual_duty_fault()->code == MANUAL_FAULT_NONE);
  fault = true; sys.ovp_latched = true;
  mode_manual_duty_end();
  assert(mode_manual_duty_fault()->code == MANUAL_FAULT_HARDWARE_OVP);
  stopped();
  setup(0);
  channel_a.telem.iin_a = .51f;
  assert(mode_manual_duty_begin() == MODE_INIT_REFUSED && starts == 0);
  stopped();
  setup(0);
  sys.vbus_mv = 35000; channel_a.telem.vin_v = 34;
  assert(mode_manual_duty_begin() == MODE_INIT_REFUSED);
  setup(0);
  reject_start = true;
  assert(mode_manual_duty_begin() == MODE_INIT_FAULT);
  stopped();

  setup(0);
  frame(0x0F, 1, 0); service_commands();
  assert(system_command_requested_mode() == MODE_NONE);
  frame(0x0F, 0, 0); service_commands();
  assert(system_command_requested_mode() == MODE_MANUAL_DUTY);
  assert(mode_manual_duty_begin() == MODE_INIT_OK);
  command_flush_all();
  frame(0x10, 2, 589); service_commands();
  assert(mode_manual_duty_target() == 589);
  frame(0x10, 0, 0); frame(0x10, 1, 12); frame(0x10, 3, 12); frame(0x10, 2, 1000);
  service_commands();
  assert(mode_manual_duty_target() == 589);
  frame(0x03, 0, 0); frame(0x10, 2, 700); frame(0x0F, 0, 0); service_commands();
  assert(system_command_requested_mode() == MODE_NONE && mode_manual_duty_target() == 589);
  assert(mode_manual_duty_service(system_command_received(SYSTEM_COMMAND_STOP)) == MODE_STATE_EXIT);
  stopped();
  puts("Manual duty lifecycle, command and protection checks passed");
}
