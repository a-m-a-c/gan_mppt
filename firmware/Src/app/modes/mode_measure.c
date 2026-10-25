#include "mode_measure.h"
#include "channel.h"
#include "system.h"
#include "control.h"
#include "mppt_v1.h"
#include "measure_io.h"
#include "pwm.h"
#include "main.h"

#define MEASURE_CHANNEL CHANNEL_A
#define START_DELAY_MS 2000U
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
  .kp = 0.02f,
  .ki = 0.4f,
  .duty_min = 0,
  .duty_max = 700,
  .po_period_ms = 100U,
  .po_step_mv = 100U,
  .po_arrived_mv = 30U,
  .po_stall_ms = 10U * 100U,
  .seed_fraction = 0.8f,
  .target_min_mv = 2000U,
  .target_max_mv = 30500U,
  .dt_max_ms = 120U,
};

typedef enum {
  MEASURE_UNPLUGGED,
  MEASURE_PENDING_START,
  MEASURE_ON,
} measure_channel_state_t;

static measure_channel_state_t state;
static uint32_t plug_time_ms;
static uint32_t last_control_ms;

mode_request_result_t mode_measure_begin(void) {
  pwm_stop_all();
  if (sys.vbus_mv > ABS_THRESHOLD * 1000.0f) return MODE_INIT_FAULT;
  if (!measure_io_begin(MEASURE_CHANNEL)) return MODE_INIT_REFUSED;
  control_init(&control_cfg);
  state = MEASURE_UNPLUGGED;
  plug_time_ms = 0U;
  last_control_ms = HAL_GetTick();
  return MODE_INIT_OK;
}

void mode_measure_end(void) {
  if (!measure_io_owned()) return;
  pwm_stop_all();
  measure_io_end();
}

mode_state_t mode_measure_service(bool stopping) {
  if (stopping) {
    mode_measure_end();
    return MODE_STATE_EXIT;
  }
  if (measure_io_failed() || sys.vbus_mv > ABS_THRESHOLD * 1000.0f) {
    mode_measure_end();
    return MODE_STATE_FAULTED;
  }
  if (!measure_io_ready()) return MODE_STATE_RUNNING;

  channel_t *chan = channel_by_id(MEASURE_CHANNEL);
  if (!chan->telem.valid) {
    mode_measure_end();
    return MODE_STATE_FAULTED;
  }
  switch (state) {
    case MEASURE_UNPLUGGED:
      if (chan->telem.vin_v > 5.0f) {
        state = MEASURE_PENDING_START;
        plug_time_ms = chan->telem.tick_ms;
      }
      break;
    case MEASURE_PENDING_START:
      if (chan->telem.vin_v <= 5.0f) {
        state = MEASURE_UNPLUGGED;
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
    // Preserve the original feedback cadence while recording faster.
    if (HAL_GetTick() - last_control_ms >= CONTROL_PERIOD_MS) {
      last_control_ms = HAL_GetTick();
      mppt_service(chan->id);
    }
    control_service(chan->id);
  }
  return MODE_STATE_RUNNING;
}
