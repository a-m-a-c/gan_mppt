#include "mode_measure.h"
#include "channel.h"
#include "system.h"
#include "control.h"
#include "mppt_v1.h"
#include "measure_io.h"
#include "pwm.h"
#include "main.h"
#include "command.h"
#include <math.h>

#define MEASURE_CHANNEL CHANNEL_A
#define MEASURE_FREQUENCY_HZ 600000U // 480 MHz HRTIM / 600 kHz = 800 period ticks.
_Static_assert(MEASURE_FREQUENCY_HZ >= PWM_MIN_FREQUENCY_HZ &&
               MEASURE_FREQUENCY_HZ <= PWM_MAX_FREQUENCY_HZ, "Measurement frequency exceeds PWM limits");
#define START_DELAY_MS 2000U
#define START_STABLE_V 0.2f // Bench settling band: at most 200 mV drift over the 2 s wait.
#define START_HEADROOM_V 0.5f // Seed at least 500 mV above Vbus*(1-Dmax); no increase to Dmax.
#define CONTROL_PERIOD_MS 40U
#define CV_THRESHOLD 50.0f
#define ABS_THRESHOLD 50.0f

static control_config_t control_cfg = {
  .ramp_limit_enabled = true,
  .thermal_limit_enabled = false,
  .dynamic_frequency_enabled = false,
  .dynamic_deadtime_enabled = false,
  .dynamic_protection_ceilings_enabled = false,
  .channel_a_enabled = MEASURE_CHANNEL == CHANNEL_A,
  .channel_b_enabled = MEASURE_CHANNEL == CHANNEL_B,
  .channel_c_enabled = MEASURE_CHANNEL == CHANNEL_C,
  .channel_d_enabled = MEASURE_CHANNEL == CHANNEL_D,
  .channel_e_enabled = MEASURE_CHANNEL == CHANNEL_E,
  .ramp_rate_per_ms = 1,
};

static const mppt_config_t mppt_cfg = {
  .kp = 0.01f,
  .ki = 0.2f,
  .duty_min = 0,
  .duty_max = 700,
  .po_period_ms = 100U,
  .po_step_mv = 100U,
  .po_arrived_mv = 75U, // Bench assumption: +/-75 mV, less than one 100 mV perturbation.
  .po_average_ms = 30U, // About 30 distinct millisecond observations per window.
  .po_stable_mv = 100U, // Bench assumption: <=100 mV peak-to-peak throughout the window.
  .po_stall_ms = 10U * 100U,
  .seed_fraction = 0.8f,
  .target_min_mv = 2000U,
  .target_max_mv = 30500U,
  .dt_max_ms = 120U,
  .preserve_pi_fraction = true,
};

typedef enum {
  MEASURE_WAIT_START,
  MEASURE_UNPLUGGED,
  MEASURE_PENDING_START,
  MEASURE_ON,
} measure_channel_state_t;

static measure_channel_state_t state;
static uint32_t plug_time_ms;
static uint32_t last_control_ms;
static uint32_t previous_frequency_hz;
static bool frequency_owned;
static float start_min_v, start_max_v;

static bool startup_voltage_ok(float vin) {
  const float minimum_target = sys.vbus_mv / 1000.0f *
    (1.0f - (float)mppt_cfg.duty_max / PWM_DUTY_SCALE) + START_HEADROOM_V;
  return isfinite(vin) && vin * mppt_cfg.seed_fraction >= minimum_target;
}

mode_request_result_t mode_measure_begin(void) {
  pwm_stop_all();
  if (sys.vbus_mv > ABS_THRESHOLD * 1000.0f) return MODE_INIT_FAULT;
  if (!measure_io_begin(MEASURE_CHANNEL)) return MODE_INIT_REFUSED;
  measure_io_set_flags(MEASURE_START_GATED);
  previous_frequency_hz = channel_by_id(MEASURE_CHANNEL)->pwm.frequency_hz;
  frequency_owned = true;
  pwm_set_frequency(MEASURE_CHANNEL, MEASURE_FREQUENCY_HZ);
  if (channel_by_id(MEASURE_CHANNEL)->pwm.frequency_hz != MEASURE_FREQUENCY_HZ) {
    mode_measure_end();
    return MODE_INIT_FAULT;
  }
  control_init(&control_cfg);
  state = MEASURE_WAIT_START;
  plug_time_ms = 0U;
  last_control_ms = HAL_GetTick();
  return MODE_INIT_OK;
}

void mode_measure_end(void) {
  if (!measure_io_owned() && !frequency_owned) return;
  pwm_stop_all();
  if (frequency_owned) {
    pwm_set_frequency(MEASURE_CHANNEL, previous_frequency_hz);
    frequency_owned = false;
  }
  if (measure_io_owned()) measure_io_end();
}

mode_state_t mode_measure_service(bool stopping) {
  if (stopping) {
    mode_measure_end();
    return MODE_STATE_EXIT;
  }
  if (pwm_faults_present() || measure_io_failed() || sys.vbus_mv > ABS_THRESHOLD * 1000.0f) {
    mode_measure_end();
    return MODE_STATE_FAULTED;
  }
  if (!measure_io_ready()) return MODE_STATE_RUNNING;

  channel_t *chan = channel_by_id(MEASURE_CHANNEL);
  if (!chan->telem.valid || !isfinite(chan->telem.vin_v)) {
    mode_measure_end();
    return MODE_STATE_FAULTED;
  }
  switch (state) {
    case MEASURE_WAIT_START:
      if (system_command_received(SYSTEM_COMMAND_START_MEASURE)) {
        measure_io_set_flags(MEASURE_START_GATED | MEASURE_START_ACCEPTED);
        state = MEASURE_UNPLUGGED;
      }
      break;
    case MEASURE_UNPLUGGED:
      if (startup_voltage_ok(chan->telem.vin_v)) {
        state = MEASURE_PENDING_START;
        plug_time_ms = chan->telem.tick_ms;
        start_min_v = start_max_v = chan->telem.vin_v;
      }
      break;
    case MEASURE_PENDING_START:
      start_min_v = fminf(start_min_v, chan->telem.vin_v);
      start_max_v = fmaxf(start_max_v, chan->telem.vin_v);
      if (!startup_voltage_ok(chan->telem.vin_v)) {
        state = MEASURE_UNPLUGGED;
      } else if (start_max_v - start_min_v > START_STABLE_V) {
        plug_time_ms = chan->telem.tick_ms;
        start_min_v = start_max_v = chan->telem.vin_v;
      } else if (chan->telem.tick_ms - plug_time_ms >= START_DELAY_MS) {
        control_start(chan->id);
        if (chan->pwm.op_state == PWM_STATE_RUNNING) {
          mppt_begin(chan->id, &mppt_cfg);
          state = MEASURE_ON;
          last_control_ms = HAL_GetTick();
        } else {
          state = MEASURE_UNPLUGGED;
        }
      }
      break;
    case MEASURE_ON:
      if (chan->telem.vin_v < 4.0f) {
        control_stop(chan->id);
        state = MEASURE_UNPLUGGED;
      }
      break;
  }

  if (state == MEASURE_ON && sys.vbus_mv <= CV_THRESHOLD * 1000.0f) {
    mppt_observe(chan->id);
    // Preserve the original feedback cadence while recording faster.
    if (HAL_GetTick() - last_control_ms >= CONTROL_PERIOD_MS) {
      last_control_ms = HAL_GetTick();
      mppt_service(chan->id);
      mppt_status_t status;
      if (mppt_get_status(chan->id, &status)) measure_io_log_mppt(&status);
    }
    control_service(chan->id);
  }
  return MODE_STATE_RUNNING;
}
