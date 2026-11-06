#include <assert.h>
#include <math.h>
#include <stdio.h>
#include "mode_pi_test.h"
#include "channel.h"
#include "system.h"
#include "control.h"
#include "command.h"
#include "measure_io.h"
#include "mppt_v1.h"
#include "pwm.h"

channel_t channel_a;
system_t sys;
static uint32_t now;
static bool owned, ready, start, fault;
static unsigned starts;
static mppt_status_t diagnostic;
uint32_t HAL_GetTick(void) { return now; }
channel_t *channel_by_id(uint32_t id) { assert(id == CHANNEL_A); return &channel_a; }
bool pwm_faults_present(void) { return fault; }
void pwm_stop_all(void) { channel_a.pwm.op_state = PWM_STATE_STOPPED; }
void pwm_set_frequency(uint32_t id, uint32_t hz) { (void)id; channel_a.pwm.frequency_hz = hz; }
bool pwm_start(uint32_t id, uint16_t duty) {
  (void)id; assert(duty == 0); starts++;
  channel_a.pwm.op_state = PWM_STATE_RUNNING; channel_a.pwm.duty_applied = duty; return true;
}
bool measure_io_begin(uint32_t id) { (void)id; owned = ready = true; return true; }
void measure_io_end(void) { owned = ready = false; }
void measure_io_set_flags(uint16_t flags) { assert(flags & MEASURE_PI_TEST); }
bool measure_io_ready(void) { return ready; }
bool measure_io_failed(void) { return false; }
void measure_io_log_mppt(const mppt_status_t *s) { diagnostic = *s; }
void control_init(control_config_t *c) { assert(c->ramp_limit_enabled && c->channel_a_enabled); }
void control_set_duty(uint32_t id, uint16_t duty) { (void)id; assert(duty <= 700); channel_a.pwm.duty_applied = duty; }
void control_service(uint32_t id) { (void)id; }
bool system_command_received(system_commands_t c) {
  assert(c == SYSTEM_COMMAND_START_MEASURE); bool value = start; start = false; return value;
}
static mode_state_t step(unsigned ms, float vin) {
  now += ms;
  channel_a.telem = (chan_telem_t){.valid=true, .tick_ms=now, .vin_v=vin, .vout_v=46, .iin_a=5, .iout_a=3};
  return mode_pi_test_service(false);
}
static void setup(void) {
  mode_pi_test_end(); now = 0; starts = 0; fault = start = false;
  channel_a.pwm.frequency_hz = 300000; sys.vbus_mv = 46000;
  assert(mode_pi_test_begin() == MODE_INIT_OK);
}
int main(void) {
  assert(!mode_pi_test_set_target(25000));
  setup();
  assert(!mode_pi_test_set_target(10000) && !mode_pi_test_set_target(31000));
  assert(mode_pi_test_set_target(25000)); step(40, 30);
  assert(diagnostic.target_mv == 25000 && (diagnostic.flags & MPPT_UPDATED));
  for (int i=0; i<60; i++) step(40, 30);
  assert(starts == 0);
  start = true; step(1, 30);
  for (int i=0; i<49; i++) step(40, 30);
  assert(starts == 0); step(40, 30); assert(starts == 1);
  for (int i=0; i<10; i++) step(40, 26);
  uint16_t old = channel_a.pwm.duty_applied;
  assert(old > 0);
  assert(mode_pi_test_set_target(27000)); step(40, 26);
  assert(diagnostic.target_mv == 27000 && diagnostic.error_mv == -1000);
  assert(channel_a.pwm.duty_applied < old);
  assert(mode_pi_test_service(true) == MODE_STATE_EXIT);
  assert(!owned && channel_a.pwm.frequency_hz == 300000 && !mode_pi_test_set_target(25000));
  setup(); fault = true; assert(step(1,30) == MODE_STATE_FAULTED); assert(!owned);
  setup(); step(1,30); now += 11; assert(mode_pi_test_service(false) == MODE_STATE_FAULTED);
  setup(); assert(step(180000,30) == MODE_STATE_FAULTED);
  setup(); assert(step(1,NAN) == MODE_STATE_FAULTED);
  puts("PI mode checks passed");
}
