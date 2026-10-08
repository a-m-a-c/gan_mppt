#include "mppt_v1.h"
#include "channel.h"
#include "control.h"
#include "main.h"
#include "pi.h"
#include "perturb_observe.h"
#include <math.h>
#include <stddef.h>

typedef struct {
  const mppt_config_t *config;
  uint32_t last_po_ms;
  uint32_t last_telem_tick;
  po_t vin_po;
  pi_t vin_pi;
} mppt_state_t;

static mppt_state_t mppt_a;
static mppt_state_t mppt_b;
static mppt_state_t mppt_c;
static mppt_state_t mppt_d;
static mppt_state_t mppt_e;

static mppt_state_t *state_by_id(uint32_t channel) {
  switch (channel) {
    case CHANNEL_A: return &mppt_a;
    case CHANNEL_B: return &mppt_b;
    case CHANNEL_C: return &mppt_c;
    case CHANNEL_D: return &mppt_d;
    case CHANNEL_E: return &mppt_e;
    default: return NULL;
  }
}

static uint32_t abs_diff(uint32_t a, uint32_t b) {
  return a > b ? a - b : b - a;
}

void mppt_begin(uint32_t channel, const mppt_config_t *config) {
  mppt_state_t *state = state_by_id(channel);
  if (!state) return;
  state->config = config;
  if (!config) return;
  const channel_t *chan = channel_by_id(channel);
  state->last_po_ms = HAL_GetTick();
  state->last_telem_tick = chan->telem.tick_ms;
  const float target_mv = floorf(chan->telem.vin_v * 1000.0f * config->seed_fraction);
  po_init(&state->vin_po, config->po_step_mv, config->target_min_mv,
          config->target_max_mv, target_mv);
  pi_init(&state->vin_pi, config->kp, config->ki, config->duty_min, config->duty_max);
}

void mppt_service(uint32_t channel) {
  mppt_state_t *state = state_by_id(channel);
  if (!state || !state->config) return;
  const channel_t *chan = channel_by_id(channel);
  if (chan->pwm.op_state != PWM_STATE_RUNNING) return;
  const mppt_config_t *config = state->config;
  const uint32_t now = HAL_GetTick();
  const uint32_t vin_mv = (uint32_t)(chan->telem.vin_v * 1000.0f);
  const bool dwelled = now - state->last_po_ms >= config->po_period_ms;
  const bool sampled = (int32_t)(chan->telem.tick_ms - state->last_po_ms) > 0;
  const bool arrived = abs_diff(vin_mv, (uint32_t)state->vin_po.target) <= config->po_arrived_mv;
  const bool stalled = now - state->last_po_ms >= config->po_stall_ms;
  if (dwelled && sampled && (arrived || stalled)) {
    state->last_po_ms = now;
    (void)po_update(&state->vin_po, chan->telem.vin_v * chan->telem.iin_a);
  }
  if (chan->telem.tick_ms != state->last_telem_tick) {
    uint32_t dt_ms = chan->telem.tick_ms - state->last_telem_tick;
    state->last_telem_tick = chan->telem.tick_ms;
    if (dt_ms > config->dt_max_ms) dt_ms = config->dt_max_ms;
    pi_track(&state->vin_pi, (float)chan->pwm.duty_applied);
    // Invert PI error: boost input voltage falls as duty rises.
    const uint16_t duty = (uint16_t)pi_update(&state->vin_pi, chan->telem.vin_v * 1000.0f,
                                             state->vin_po.target, (float)dt_ms);
    control_set_duty(channel, duty);
  }
}
