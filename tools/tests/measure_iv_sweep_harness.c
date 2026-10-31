#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "channel.h"
#include "system.h"
#include "pwm.h"
#include "measure_io.h"
#include "mode_measure_iv_sweep.h"

channel_t channel_a, channel_b, channel_c, channel_d, channel_e;
system_t sys;
static uint32_t now_ms;
static bool owned, ready, io_failed, hw_fault, bypass, reject_start;
static bool low_current, reject_step, clamp_sweep_frequency;
static uint32_t last_pwm_step_ms;
static unsigned starts, ends, max_duty, up_steps, down_steps;
static uint16_t flags;
static float output_v;
#define SWEEP_FLAGS (MEASURE_IV_SWEEP | MEASURE_IV_DUTY_RAMP)

uint32_t HAL_GetTick(void) { return now_ms; }
channel_t *channel_by_id(uint32_t ch) { assert(ch == CHANNEL_A); return &channel_a; }
bool pwm_faults_present(void) { return hw_fault; }
void enable_bypass(bool enabled) { bypass = enabled; }
bool pwm_start(uint32_t ch, uint16_t duty) {
  assert(ch == CHANNEL_A && duty == 0);
  assert(channel_a.pwm.frequency_hz == 600000);
  starts++;
  if (reject_start) return false;
  channel_a.pwm.op_state = PWM_STATE_RUNNING;
  channel_a.pwm.duty_applied = duty;
  last_pwm_step_ms = now_ms;
  return true;
}
void pwm_stop(uint32_t ch) { assert(ch == CHANNEL_A); channel_a.pwm.op_state = PWM_STATE_STOPPED; }
void pwm_stop_all(void) {
  channel_a.pwm.op_state = channel_b.pwm.op_state = channel_c.pwm.op_state = PWM_STATE_STOPPED;
  channel_d.pwm.op_state = channel_e.pwm.op_state = PWM_STATE_STOPPED;
}
bool pwm_set_duty_cycle(uint32_t ch, uint16_t duty) {
  assert(ch == CHANNEL_A && duty <= 700);
  assert(channel_a.pwm.op_state == PWM_STATE_RUNNING);
  if (flags & MEASURE_IV_DESCENDING) {
    assert(duty + 1 == channel_a.pwm.duty_applied);
    down_steps++;
  } else {
    assert(duty == channel_a.pwm.duty_applied + 1);
    up_steps++;
  }
  assert(now_ms - last_pwm_step_ms >= 100);
  if (reject_step) return false;
  last_pwm_step_ms = now_ms;
  channel_a.pwm.duty_applied = duty;
  if (duty > max_duty) max_duty = duty;
  return true;
}
void pwm_set_frequency(uint32_t ch, uint32_t hz) {
  assert(ch == CHANNEL_A && channel_a.pwm.op_state != PWM_STATE_RUNNING);
  // Match the real driver's configured clamp, with an injected old ceiling.
  const uint32_t cap = clamp_sweep_frequency ? 400000U : PWM_MAX_FREQUENCY_HZ;
  channel_a.pwm.frequency_hz = hz < PWM_MIN_FREQUENCY_HZ ? PWM_MIN_FREQUENCY_HZ : hz > cap ? cap : hz;
}
bool measure_io_begin(uint32_t ch) {
  assert(ch == CHANNEL_A);
  if (owned) return false;
  flags = 0;
  owned = true;
  return true;
}
bool measure_io_owned(void) { return owned; }
bool measure_io_ready(void) { return ready; }
bool measure_io_failed(void) { return io_failed; }
void measure_io_set_flags(uint16_t value) { flags = value; }
void measure_io_end(void) { ends++; owned = ready = false; }

static void telemetry(void) {
  bool on = channel_a.pwm.op_state == PWM_STATE_RUNNING;
  channel_a.telem = (chan_telem_t){
    .vin_v = on ? fminf(29.02f, output_v * (1.0f - channel_a.pwm.duty_applied / 1000.0f)) : 29.02f,
    .iin_a = on ? (low_current ? 1.0f : fminf(8.66f, channel_a.pwm.duty_applied * 0.015f)) : 0.0f,
    .vout_v = on ? output_v : 29.02f,
    .iout_a = on ? 4.0f : 0.0f,
    .tick_ms = now_ms, .valid = true,
  };
}
static void setup(uint32_t tick) {
  memset(&channel_a, 0, sizeof(channel_a));
  now_ms = tick;
  owned = io_failed = hw_fault = reject_start = low_current = reject_step = clamp_sweep_frequency = false;
  ready = bypass = true;
  starts = ends = max_duty = flags = 0;
  up_steps = down_steps = 0;
  sys.vbus_mv = 47000;
  output_v = 47.0f;
  channel_a.pwm.frequency_hz = 300000; // A different entry setting must be restored.
  assert(mode_measure_iv_sweep_begin() == MODE_INIT_OK);
  assert(!bypass && flags == SWEEP_FLAGS && channel_a.pwm.frequency_hz == 600000);
  telemetry();
}
static mode_state_t pump(void) {
  now_ms++;
  telemetry();
  return mode_measure_iv_sweep_service(false);
}
static void run_until_switching(void) {
  for (unsigned i = 0; i < 2100 && !starts; i++) assert(pump() == MODE_STATE_RUNNING);
  assert(starts == 1 && channel_a.pwm.op_state == PWM_STATE_RUNNING);
  telemetry();
}
static void assert_stopped(void) {
  assert(channel_a.pwm.op_state != PWM_STATE_RUNNING);
  assert(channel_b.pwm.op_state != PWM_STATE_RUNNING && channel_c.pwm.op_state != PWM_STATE_RUNNING);
  assert(channel_d.pwm.op_state != PWM_STATE_RUNNING && channel_e.pwm.op_state != PWM_STATE_RUNNING);
  assert(!owned && !bypass && ends == 1);
  assert(channel_a.pwm.frequency_hz == 300000);
}

int main(void) {
  for (unsigned wrap = 0; wrap < 2; wrap++) {
    setup(wrap ? UINT32_MAX - 1000U : 0U);
    unsigned elapsed = 0;
    mode_state_t result = MODE_STATE_RUNNING;
    while (result == MODE_STATE_RUNNING && elapsed++ < 86000) result = pump();
    assert(result == MODE_STATE_EXIT && elapsed > 72500 && elapsed < 73000);
    assert(max_duty == 700 && starts == 1 && up_steps == 700 && down_steps == 0);
    assert(channel_a.pwm.duty_applied == 700);
    assert(now_ms - last_pwm_step_ms == 600U); // Endpoint hold + post capture.
    assert(flags == (SWEEP_FLAGS | MEASURE_IV_COMPLETE));
    assert_stopped();
  }
  const unsigned cancel_times[] = {0, 1, 1999, 2100, 5000, 18000, 30000, 72000, 72500};
  for (unsigned n = 0; n < sizeof(cancel_times) / sizeof(cancel_times[0]); n++) {
    setup(0);
    for (unsigned i = 0; i < cancel_times[n]; i++) assert(pump() == MODE_STATE_RUNNING);
    assert(mode_measure_iv_sweep_service(true) == MODE_STATE_EXIT);
    assert(!(flags & MEASURE_IV_COMPLETE));
    assert_stopped();
  }
  for (unsigned failure = 0; failure < 20; failure++) {
    setup(0);
    run_until_switching();
    switch (failure) {
      case 0: sys.vbus_mv = 50000; break;
      case 1: sys.vbus_mv = 34999; break;
      case 2: channel_a.telem.iin_a = 9.5f; break;
      case 3: channel_a.telem.iout_a = 8.0f; break;
      case 4: channel_a.telem.vin_v = 9.99f; break;
      case 5: channel_a.telem.vin_v = 35.0f; break;
      case 6: channel_a.telem.vout_v = 50.0f; break;
      case 7: channel_a.telem.iin_a = -INFINITY; break;
      case 8: channel_a.telem.iout_a = -INFINITY; break;
      case 9: channel_a.telem.vin_v = NAN; break;
      case 10: channel_a.telem.iin_a = NAN; break;
      case 11: channel_a.telem.vout_v = NAN; break;
      case 12: channel_a.telem.iout_a = NAN; break;
      case 13: channel_a.telem.tick_ms -= 11; break;
      case 14: ready = false; break;
      case 15: io_failed = true; break;
      case 16: hw_fault = true; break;
      case 17: channel_a.pwm.op_state = PWM_STATE_STOPPED; break;
      case 18: channel_a.pwm.duty_applied = 701; break;
      case 19: channel_a.telem.vout_v = 48.01f; break;
    }
    assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
    assert(!(flags & MEASURE_IV_COMPLETE));
    assert_stopped();
  }
  setup(0);
  ready = false;
  now_ms = 2000;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
  assert(starts == 0);
  assert_stopped();
  setup(0);
  now_ms = 85000;
  telemetry();
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
  assert_stopped();
  setup(0);
  channel_a.telem.iin_a = 0.6f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
  assert(starts == 0);
  assert_stopped();
  setup(0);
  assert(pump() == MODE_STATE_RUNNING);
  reject_start = true;
  now_ms += 2000;
  telemetry();
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
  assert_stopped();
  // Start at zero even with a charged output.
  setup(0);
  assert(pump() == MODE_STATE_RUNNING);
  now_ms += 2000;
  telemetry();
  channel_a.telem.vout_v = 47.0f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  assert(channel_a.pwm.duty_applied == 0);
  assert(mode_measure_iv_sweep_service(true) == MODE_STATE_EXIT);
  assert_stopped();
  // The old 7.5 A endpoint no longer terminates the duty sweep.
  setup(0);
  run_until_switching();
  channel_a.telem.iout_a = 7.5f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  channel_a.telem.iout_a = 0.0f;
  channel_a.telem.iin_a = 7.499f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  assert(channel_a.pwm.op_state == PWM_STATE_RUNNING);
  channel_a.telem.iin_a = 7.5f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  assert(channel_a.pwm.op_state == PWM_STATE_RUNNING && max_duty == 0);
  channel_a.telem.iin_a = 8.5f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  assert(!(flags & MEASURE_IV_COMPLETE));
  assert(mode_measure_iv_sweep_service(true) == MODE_STATE_EXIT);
  assert_stopped();
  // A delayed pass must never issue catch-up steps.
  setup(0);
  run_until_switching();
  now_ms += 500;
  telemetry();
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  assert(channel_a.pwm.duty_applied == 1);
  for (unsigned i = 0; i < 99; i++) assert(pump() == MODE_STATE_RUNNING);
  assert(channel_a.pwm.duty_applied == 1);
  assert(pump() == MODE_STATE_RUNNING && channel_a.pwm.duty_applied == 2);
  reject_step = true;
  now_ms += 100;
  telemetry();
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
  assert_stopped();
  // A complete upward sweep at a different valid battery voltage.
  setup(0);
  low_current = true;
  output_v = 49.0f;
  sys.vbus_mv = 49000;
  mode_state_t result = MODE_STATE_RUNNING;
  for (unsigned i = 0; i < 86000 && result == MODE_STATE_RUNNING; i++) result = pump();
  assert(result == MODE_STATE_EXIT && max_duty == 700);
  assert(now_ms - last_pwm_step_ms == 600U); // Final hold + post capture.
  assert(flags == (SWEEP_FLAGS | MEASURE_IV_COMPLETE));
  assert_stopped();
  // The old 12 V normal endpoint no longer prevents reaching 70%.
  setup(0);
  run_until_switching();
  channel_a.telem.vin_v = 12.001f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  assert(channel_a.pwm.op_state == PWM_STATE_RUNNING);
  channel_a.telem.vin_v = 12.0f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
  assert(channel_a.pwm.op_state == PWM_STATE_RUNNING && max_duty == 0);
  assert(mode_measure_iv_sweep_service(true) == MODE_STATE_EXIT);
  assert_stopped();
  // The 10 V fault floor still prevents starting an invalid source.
  setup(0);
  channel_a.telem.vin_v = 9.99f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
  assert(starts == 0);
  assert_stopped();
  // Finite reverse readings no longer trip the sweep; idle checks still apply.
  const float reverse_readings[] = {-10.0f, -10.887f, -10.365f, -20.0f};
  for (unsigned n = 0; n < sizeof(reverse_readings) / sizeof(reverse_readings[0]); n++) {
    for (unsigned output = 0; output < 2; output++) {
      setup(0);
      run_until_switching();
      if (output) channel_a.telem.iout_a = reverse_readings[n];
      else channel_a.telem.iin_a = reverse_readings[n];
      assert(mode_measure_iv_sweep_service(false) == MODE_STATE_RUNNING);
      assert(channel_a.pwm.op_state == PWM_STATE_RUNNING);
      hw_fault = true;
      assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
      assert_stopped();
    }
  }
  for (unsigned output = 0; output < 2; output++) {
    setup(0);
    if (output) channel_a.telem.iout_a = -0.501f;
    else channel_a.telem.iin_a = -0.501f;
    assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
    assert(starts == 0);
    assert_stopped();
  }
  sys.vbus_mv = 50000;
  assert(mode_measure_iv_sweep_begin() == MODE_INIT_REFUSED && !owned);
  // Protection remains live during the final endpoint hold.
  setup(0);
  for (unsigned i = 0; i < 72050; i++) assert(pump() == MODE_STATE_RUNNING);
  assert(up_steps == 700 && down_steps == 0);
  channel_a.telem.iin_a = 9.5f;
  assert(mode_measure_iv_sweep_service(false) == MODE_STATE_FAULTED);
  assert(!(flags & MEASURE_IV_COMPLETE));
  assert_stopped();
  // The application's external hardware-fault cleanup restores frequency too.
  setup(0);
  run_until_switching();
  pwm_stop_all();
  mode_measure_iv_sweep_end();
  assert_stopped();
  // A clamped frequency must be rejected before any PWM start, and restored.
  starts = ends = 0;
  clamp_sweep_frequency = true;
  assert(mode_measure_iv_sweep_begin() == MODE_INIT_FAULT);
  assert(starts == 0);
  assert_stopped();
  puts("IV sweep lifecycle checks passed");
  return 0;
}
