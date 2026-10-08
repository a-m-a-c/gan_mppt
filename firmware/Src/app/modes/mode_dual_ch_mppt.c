#include "mode_dual_ch_mppt.h"
#include "main.h"
#include "system.h"
#include "channel.h"
#include "control.h"
#include "mppt_v1.h"
#include <stdint.h>

static control_config_t control_cfg = {
  .ramp_limit_enabled = true,
  .channel_a_enabled = true,
  .channel_e_enabled = true,
  // Lowest integer rate: 1 unit/ms versus the old nominal 20 units/40 ms.
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

#define TELEM_MAX_AGE_MS 120U
// Bench-only ceiling.
#define MAX_OUTPUT_MV 14600U

static bool telem_is_fresh(uint32_t now) {
  return channel_a.telem.valid && ((now - channel_a.telem.tick_ms) <= TELEM_MAX_AGE_MS)
      && channel_e.telem.valid && ((now - channel_e.telem.tick_ms) <= TELEM_MAX_AGE_MS);
}

static mode_state_t finish(mode_state_t state) {
  control_stop_all();
  return state;
}

mode_request_result_t mode_dual_ch_mppt_begin(void) {
  const uint32_t now = HAL_GetTick();

  if (!telem_is_fresh(now)) return MODE_INIT_REFUSED;

  mppt_begin(CHANNEL_A, &mppt_cfg);
  mppt_begin(CHANNEL_E, &mppt_cfg);

  control_init(&control_cfg);
  control_start(CHANNEL_A);
  control_start(CHANNEL_E);
  if (channel_a.pwm.op_state != PWM_STATE_RUNNING || channel_e.pwm.op_state != PWM_STATE_RUNNING) {
    control_stop_all();
    return MODE_INIT_REFUSED;
  }

  return MODE_INIT_OK;
}

mode_state_t mode_dual_ch_mppt_service(bool stopping) {
  const uint32_t now = HAL_GetTick();

  if (sys.ovp_latched || channel_a.pwm.ocp_latched || channel_e.pwm.ocp_latched) {
    return finish(MODE_STATE_FAULTED);
  }

  if (stopping) {
    return finish(MODE_STATE_EXIT);
  }

  if (!telem_is_fresh(now)) {
    return finish(MODE_STATE_FAULTED);
  }

  // Use the 1 ms ADC reading for load-loss protection; INA228 updates every 40 ms.

  if (sys.vbus_mv >= MAX_OUTPUT_MV) {
    return finish(MODE_STATE_FAULTED);
  }

  mppt_service(CHANNEL_A);
  mppt_service(CHANNEL_E);
  control_service(CHANNEL_A);
  control_service(CHANNEL_E);
  return MODE_STATE_RUNNING;
}
