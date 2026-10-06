#include "mode_single_ch_5_mppt.h"
#include "main.h"
#include "system.h"
#include "channel.h"
#include "control.h"
#include "pi.h"
#include "perturb_observe.h"
#include <math.h>
#include <stdint.h>

static control_config_t control_cfg = {
  .ramp_limit_enabled = true,
  .channel_e_enabled = true,
  // Lowest integer rate: 1 unit/ms versus the old nominal 20 units/40 ms.
  .ramp_rate_per_ms = 1,
};

#define PO_PERIOD_MS 100U
#define PO_STEP_MV   100U
#define KP 0.02f
#define KI 0.4f
#define MAX_DUTY_CYCLE 700
#define MIN_DUTY_CYCLE 0

#define PO_ARRIVED_MV 30U

#define PO_STALL_MS (10U * PO_PERIOD_MS)

#define TELEM_MAX_AGE_MS 120U
#define DT_MAX_MS        TELEM_MAX_AGE_MS

#define IIN_IDLE_MAX_A 1.0f

// Bench-only ceiling copied from channel-1 MPPT.
#define MAX_OUTPUT_MV 14600U

#define PO_SEED_FRACTION 0.8f

#define VIN_TARGET_MIN_MV 2000
#define VIN_TARGET_MAX_MV 30500

static uint32_t last_po_ms;
static uint32_t last_telem_tick;
static po_t vin_po;
static pi_t vin_pi;

static bool telem_is_fresh(uint32_t now) {
  return channel_e.telem.valid && ((now - channel_e.telem.tick_ms) <= TELEM_MAX_AGE_MS);
}

static uint32_t abs_diff(uint32_t a, uint32_t b) {
  return (a > b) ? (a - b) : (b - a);
}

static mode_state_t finish(mode_state_t state) {
  control_stop();
  return state;
}

mode_request_result_t mode_single_ch_5_mppt_begin(void) {
  const uint32_t now = HAL_GetTick();

  if (!telem_is_fresh(now)) return MODE_INIT_REFUSED;

  last_po_ms = now;
  last_telem_tick = channel_e.telem.tick_ms;

  const float initial_target_mv = floorf(channel_e.telem.vin_v * 1000.0f * PO_SEED_FRACTION);
  po_init(&vin_po, PO_STEP_MV, VIN_TARGET_MIN_MV, VIN_TARGET_MAX_MV, initial_target_mv);

  pi_init(&vin_pi, KP, KI, (float)MIN_DUTY_CYCLE, (float)MAX_DUTY_CYCLE);

  control_init(&control_cfg);
  control_start();
  if (channel_e.pwm.op_state != PWM_STATE_RUNNING) return MODE_INIT_REFUSED;

  return MODE_INIT_OK;
}

mode_state_t mode_single_ch_5_mppt_service(bool stopping) {
  const uint32_t now = HAL_GetTick();

  if (sys.ovp_latched || channel_e.pwm.ocp_latched) {
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

  const uint32_t vin_mv = (uint32_t)(channel_e.telem.vin_v * 1000.0f);
  const bool dwelled = (now - last_po_ms) >= PO_PERIOD_MS;
  const bool sampled = (int32_t)(channel_e.telem.tick_ms - last_po_ms) > 0;
  const bool arrived = abs_diff(vin_mv, (uint32_t)vin_po.target) <= PO_ARRIVED_MV;
  const bool stalled = (now - last_po_ms) >= PO_STALL_MS;

  if (dwelled && sampled && (arrived || stalled)) {
    last_po_ms = now;
    const float pin_w = channel_e.telem.vin_v * channel_e.telem.iin_a;

    (void)po_update(&vin_po, pin_w);
  }

  if (channel_e.telem.tick_ms != last_telem_tick) {
    uint32_t dt_ms = channel_e.telem.tick_ms - last_telem_tick;
    last_telem_tick = channel_e.telem.tick_ms;
    if (dt_ms > DT_MAX_MS) {
      dt_ms = DT_MAX_MS;
    }

    pi_track(&vin_pi, (float)channel_e.pwm.duty_applied);
    // Invert PI error: boost input voltage falls as duty rises.
    uint16_t duty = (uint16_t)pi_update(&vin_pi, channel_e.telem.vin_v * 1000.0f,
                                        vin_po.target, (float)dt_ms);

    control_set_duty(CHANNEL_E, duty);
  }

  control_service();
  return MODE_STATE_RUNNING;
}
