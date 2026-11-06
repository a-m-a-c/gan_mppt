#include "mode_pi_test.h"
#include "channel.h"
#include "system.h"
#include "control.h"
#include "pi.h"
#include "mppt_v1.h"
#include "measure_io.h"
#include "pwm.h"
#include "main.h"
#include "command.h"
#include <math.h>

#define PI_TEST_FREQUENCY_HZ 600000U // 480 MHz / 600 kHz = 800 ticks.
#define PI_TEST_DUTY_MAX 700U
#define PI_TEST_PERIOD_MS 40U
#define PI_TEST_TIMEOUT_MS 180000U // Three minutes bounds an unattended test.

static control_config_t config = {
  .ramp_limit_enabled = true, .channel_a_enabled = true, .ramp_rate_per_ms = 1,
};
static pi_t pi;
static bool owned, released, running, target_pending;
static uint16_t target_mv, pending_mv, requested_duty;
static uint32_t entered_ms, stable_ms, last_control_ms, last_sample_ms, previous_frequency;
static float min_v, max_v;
static mppt_status_t status;

static bool target_ok(uint16_t mv) {
  // D <= 0.70 requires Vin >= 0.30*Vbus; retain 0.5 V bench headroom.
  return mv >= 2000U && mv <= 30500U &&
    mv >= sys.vbus_mv * (1.0f - (float)PI_TEST_DUTY_MAX / PWM_DUTY_SCALE) + 500.0f;
}

bool mode_pi_test_set_target(uint16_t mv) {
  if (!owned || !target_ok(mv)) return false;
  pending_mv = mv;
  target_pending = true;
  return true;
}

mode_request_result_t mode_pi_test_begin(void) {
  pwm_stop_all();
  if (sys.vbus_mv > 50000U) return MODE_INIT_FAULT;
  if (!measure_io_begin(CHANNEL_A)) return MODE_INIT_REFUSED;
  owned = true;
  released = running = target_pending = false;
  target_mv = pending_mv = requested_duty = 0U;
  status = (mppt_status_t){0};
  entered_ms = stable_ms = last_control_ms = HAL_GetTick();
  min_v = INFINITY;
  max_v = -INFINITY;
  previous_frequency = channel_by_id(CHANNEL_A)->pwm.frequency_hz;
  measure_io_set_flags(MEASURE_PI_TEST | MEASURE_START_GATED);
  pwm_set_frequency(CHANNEL_A, PI_TEST_FREQUENCY_HZ);
  if (channel_by_id(CHANNEL_A)->pwm.frequency_hz != PI_TEST_FREQUENCY_HZ) {
    mode_pi_test_end();
    return MODE_INIT_FAULT;
  }
  control_init(&config);
  pi_init(&pi, 0.002f, 0.3f, 0.0f, PI_TEST_DUTY_MAX);
  return MODE_INIT_OK;
}

void mode_pi_test_end(void) {
  if (!owned) return;
  pwm_stop_all();
  pwm_set_frequency(CHANNEL_A, previous_frequency);
  measure_io_end();
  owned = released = running = target_pending = false;
}

mode_state_t mode_pi_test_service(bool stopping) {
  if (stopping) {
    mode_pi_test_end();
    return MODE_STATE_EXIT;
  }
  const uint32_t now = HAL_GetTick();
  channel_t *ch = channel_by_id(CHANNEL_A);
  if (pwm_faults_present() || measure_io_failed() ||
      sys.vbus_mv > 50000.0f || now - entered_ms >= PI_TEST_TIMEOUT_MS) goto fault;
  if (!measure_io_ready()) {
    if (now - entered_ms > 1000U) goto fault;
    return MODE_STATE_RUNNING;
  }
  if (!ch->telem.valid || !isfinite(ch->telem.vin_v) || !isfinite(ch->telem.iin_a) ||
      !isfinite(ch->telem.vout_v) || !isfinite(ch->telem.iout_a) ||
      now - ch->telem.tick_ms > 10U || ch->telem.vin_v > 35.0f ||
      ch->telem.vout_v > 50.0f || ch->telem.iin_a > 9.5f ||
      ch->telem.iout_a > 8.0f || ch->telem.iin_a < -10.0f || ch->telem.iout_a < -10.0f)
    goto fault;
  if (target_mv && !target_ok(target_mv)) goto fault;
  if (system_command_received(SYSTEM_COMMAND_START_MEASURE) && target_mv) {
    released = true;
    stable_ms = now;
    min_v = INFINITY;
    max_v = -INFINITY;
    measure_io_set_flags(MEASURE_PI_TEST | MEASURE_START_GATED | MEASURE_START_ACCEPTED);
  }
  if (released && !running) {
    min_v = fminf(min_v, ch->telem.vin_v);
    max_v = fmaxf(max_v, ch->telem.vin_v);
    if (ch->telem.vin_v * 1000.0f < target_mv || max_v - min_v > 0.2f) {
      stable_ms = now;
      min_v = max_v = ch->telem.vin_v;
    } else if (now - stable_ms >= 2000U) {
      // Begin at zero duty; subsequent requests pass through the existing ramp.
      if (!pwm_start(CHANNEL_A, 0U)) goto fault;
      pi_reset(&pi);
      pi_track(&pi, 0.0f);
      requested_duty = 0U;
      last_sample_ms = ch->telem.tick_ms;
      running = true;
    }
  }
  if (running && (ch->pwm.op_state != PWM_STATE_RUNNING || ch->telem.vin_v < 4.0f)) goto fault;
  if (now - last_control_ms >= PI_TEST_PERIOD_MS) {
    last_control_ms = now;
    status.flags = MPPT_POWER_VALID;
    if (target_pending) {
      if (!target_ok(pending_mv)) goto fault;
      target_mv = pending_mv;
      target_pending = false;
      status.decisions++;
      status.flags |= MPPT_UPDATED;
    }
    if (running && ch->telem.tick_ms != last_sample_ms) {
      uint32_t dt = ch->telem.tick_ms - last_sample_ms;
      last_sample_ms = ch->telem.tick_ms;
      if (dt > 120U) dt = 120U;
      if (ch->pwm.duty_applied != requested_duty) {
        pi_track(&pi, ch->pwm.duty_applied);
        status.flags |= MPPT_PI_TRACKING;
      }
      // Increasing boost duty lowers input voltage: invert the PI error.
      requested_duty = (uint16_t)pi_update(&pi, ch->telem.vin_v * 1000.0f, target_mv, dt);
      control_set_duty(CHANNEL_A, requested_duty);
    }
    status.target_mv = target_mv;
    status.error_mv = (int32_t)(ch->telem.vin_v * 1000.0f - target_mv);
    status.power_w = ch->telem.vin_v * ch->telem.iin_a;
    status.compared_target_mv = target_mv;
    // MPD1 is reused for PI diagnostics; decisions counts applied reference changes.
    measure_io_log_mppt(&status);
  }
  if (running) control_service(CHANNEL_A);
  return MODE_STATE_RUNNING;
fault:
  mode_pi_test_end();
  return MODE_STATE_FAULTED;
}
