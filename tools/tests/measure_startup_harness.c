#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "channel.h"
#include "command.h"
#include "control.h"
#include "measure_io.h"
#include "mode_measure.h"
#include "mppt_v1.h"
#include "pwm.h"
#include "system.h"
#include "transport.h"

channel_t channel_a, channel_b, channel_c, channel_d, channel_e;
system_t sys;
static uint32_t now;
static bool owned, ready, io_failed, hw_fault;
static unsigned starts, mppt_starts, ends;
static uint16_t flags;
static float seeded_v;
static transport_frame_t frame;
static bool pending_frame;

uint32_t HAL_GetTick(void) { return now; }
channel_t *channel_by_id(uint32_t ch) { assert(ch == CHANNEL_A); return &channel_a; }
bool pwm_faults_present(void) { return hw_fault; }
void pwm_stop_all(void) {
  channel_a.pwm.op_state = channel_b.pwm.op_state = channel_c.pwm.op_state = PWM_STATE_STOPPED;
  channel_d.pwm.op_state = channel_e.pwm.op_state = PWM_STATE_STOPPED;
}
void pwm_set_frequency(uint32_t ch, uint32_t hz) {
  assert(ch == CHANNEL_A && channel_a.pwm.op_state != PWM_STATE_RUNNING);
  channel_a.pwm.frequency_hz = hz;
}
bool measure_io_begin(uint32_t ch) {
  assert(ch == CHANNEL_A);
  if (owned) return false;
  owned = ready = true;
  return true;
}
void measure_io_end(void) { owned = ready = false; ends++; }
void measure_io_set_flags(uint16_t value) { flags = value; }
bool measure_io_owned(void) { return owned; }
bool measure_io_ready(void) { return ready; }
bool measure_io_failed(void) { return io_failed; }
void control_init(control_config_t *cfg) {
  assert(cfg->channel_a_enabled && !cfg->channel_b_enabled && !cfg->channel_c_enabled &&
         !cfg->channel_d_enabled && !cfg->channel_e_enabled);
}
void control_start(uint32_t ch) {
  assert(ch == CHANNEL_A && (flags & MEASURE_START_ACCEPTED));
  starts++;
  channel_a.pwm.op_state = PWM_STATE_RUNNING;
}
void control_stop(uint32_t ch) { assert(ch == CHANNEL_A); channel_a.pwm.op_state = PWM_STATE_STOPPED; }
void control_service(uint32_t ch) { assert(ch == CHANNEL_A); }
void mppt_begin(uint32_t ch, const mppt_config_t *cfg) {
  assert(ch == CHANNEL_A && cfg->duty_max == 700);
  seeded_v = channel_a.telem.vin_v * cfg->seed_fraction;
  assert(seeded_v >= sys.vbus_mv / 1000.0f * (1.0f - cfg->duty_max / 1000.0f) + .499f);
  mppt_starts++;
}
void mppt_service(uint32_t ch) { assert(ch == CHANNEL_A); }
void mppt_observe(uint32_t ch) { assert(ch == CHANNEL_A); }
bool mppt_get_status(uint32_t ch, mppt_status_t *status) {
  assert(ch == CHANNEL_A);
  *status = (mppt_status_t){0};
  return true;
}
void measure_io_log_mppt(const mppt_status_t *status) { assert(status); }
bool mode_manual_duty_set(uint16_t duty) { (void)duty; return false; }
bool mode_pi_test_set_target(uint16_t mv) { (void)mv; return false; }
bool serial_take_next_frame(transport_frame_t *out) {
  if (!pending_frame) return false;
  *out = frame;
  pending_frame = false;
  return true;
}
static void command(uint8_t op, uint8_t len) {
  frame = (transport_frame_t){.op = op, .len = len};
  pending_frame = true;
  command_service();
}
static mode_state_t pump(uint32_t delta, float vin) {
  now += delta;
  channel_a.telem = (chan_telem_t){.vin_v = vin, .vout_v = 45, .valid = true, .tick_ms = now};
  mode_state_t result = mode_measure_service(system_command_received(SYSTEM_COMMAND_STOP));
  command_flush_all();
  return result;
}
static void setup(uint32_t tick) {
  if (owned) mode_measure_end();
  memset(&channel_a, 0, sizeof(channel_a));
  now = tick;
  owned = io_failed = hw_fault = pending_frame = false;
  starts = mppt_starts = ends = 0;
  flags = 0;
  sys.vbus_mv = 45000;
  channel_a.pwm.frequency_hz = 300000;
  command_init();
  assert(mode_measure_begin() == MODE_INIT_OK);
  assert(flags == MEASURE_START_GATED && channel_a.pwm.frequency_hz == 600000);
}
static void stopped(void) {
  assert(!owned && !ready && ends == 1);
  assert(channel_a.pwm.op_state != PWM_STATE_RUNNING && channel_a.pwm.frequency_hz == 300000);
}

int main(void) {
  for (unsigned wrap = 0; wrap < 2; wrap++) {
    setup(wrap ? UINT32_MAX - 1000U : 0U);
    // Even a charged input cannot arm PWM during the capture-only baseline.
    for (unsigned n = 0; n < 20000; n++) assert(pump(1, n < 10000 ? 6.069f : 27.0f) == MODE_STATE_RUNNING);
    assert(starts == 0);
    // Invalid payload is ignored. A valid start command releases the gate.
    command(0x11, 1);
    assert(pump(1, 27) == MODE_STATE_RUNNING && flags == MEASURE_START_GATED);
    command(0x11, 0);
    assert(pump(1, 6.069f) == MODE_STATE_RUNNING);
    assert(flags == (MEASURE_START_GATED | MEASURE_START_ACCEPTED));
    // Reproduce the old zero-current seed input after release: still cannot start.
    for (unsigned n = 0; n < 4000; n++) assert(pump(1, 6.069f) == MODE_STATE_RUNNING);
    assert(starts == 0);
    // Adequate voltage still needs to settle: a rising input resets the window.
    for (unsigned n = 0; n < 2500; n++) assert(pump(1, 20.0f + n * .002f) == MODE_STATE_RUNNING);
    assert(starts == 0);
    for (unsigned n = 0; n < 1999; n++) assert(pump(1, 27) == MODE_STATE_RUNNING);
    assert(starts == 0);
    for (unsigned n = 0; n < 3; n++) assert(pump(1, 27) == MODE_STATE_RUNNING);
    assert(starts == 1 && mppt_starts == 1 && fabsf(seeded_v - 21.6f) < .01f);
    // On unplug/replug, the reference is seeded from the newly settled voltage.
    assert(pump(1, 0) == MODE_STATE_RUNNING);
    assert(channel_a.pwm.op_state == PWM_STATE_STOPPED);
    for (unsigned n = 0; n < 2002; n++) assert(pump(1, 26) == MODE_STATE_RUNNING);
    assert(starts == 2 && mppt_starts == 2 && fabsf(seeded_v - 20.8f) < .01f);
    command(0x03, 0);
    command(0x11, 0);
    assert(!system_command_received(SYSTEM_COMMAND_START_MEASURE));
    assert(pump(1, 26) == MODE_STATE_EXIT);
    stopped();
    mode_measure_end();
    assert(ends == 1);
  }
  // Fault and STOP cleanup also work while waiting for start.
  for (unsigned failure = 0; failure < 4; failure++) {
    setup(0);
    if (failure == 0) command(0x03, 0);
    if (failure == 1) hw_fault = true;
    if (failure == 2) io_failed = true;
    if (failure == 3) sys.vbus_mv = 50001;
    assert(pump(1, 27) == (failure == 0 ? MODE_STATE_EXIT : MODE_STATE_FAULTED));
    assert(starts == 0);
    stopped();
  }
  puts("Measurement gated startup checks passed");
  return 0;
}
