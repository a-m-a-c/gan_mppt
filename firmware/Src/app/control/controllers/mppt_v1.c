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
  uint32_t observed_tick, window_start, window_count;
  float power_sum, min_mv, max_mv;
  uint16_t last_requested_duty;
  mppt_status_t status;
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
  state->observed_tick = chan->telem.tick_ms;
  state->window_count = 0U;
  state->status = (mppt_status_t){0};
  state->last_requested_duty = chan->pwm.duty_applied;
  const float target_mv = floorf(chan->telem.vin_v * 1000.0f * config->seed_fraction);
  po_init(&state->vin_po, config->po_step_mv, config->target_min_mv,
          config->target_max_mv, target_mv);
  pi_init(&state->vin_pi, config->kp, config->ki, config->duty_min, config->duty_max);
  if (config->preserve_pi_fraction) pi_track(&state->vin_pi, chan->pwm.duty_applied);
}

void mppt_observe(uint32_t channel) {
  mppt_state_t *state = state_by_id(channel);
  if (!state || !state->config || !state->config->po_average_ms) return;
  const channel_t *chan = channel_by_id(channel);
  const chan_telem_t *sample = &chan->telem;
  const uint32_t tick = sample->tick_ms;
  // At about 1.3 kHz acquisition, allow at most 3 ms between observations.
  if (chan->pwm.op_state != PWM_STATE_RUNNING || !sample->valid ||
      !isfinite(sample->vin_v) || !isfinite(sample->iin_a) ||
      HAL_GetTick() - tick > 3U) {
    state->window_count = 0U;
    return;
  }
  if (tick == state->observed_tick) return;
  if (tick - state->observed_tick > 3U) state->window_count = 0U;
  state->observed_tick = tick;
  const float mv = sample->vin_v * 1000.0f;
  // Exclude the first 20 ms after each perturbation, even if already in band.
  if ((int32_t)(tick - state->last_po_ms) < 20 ||
      fabsf(mv - state->vin_po.target) > state->config->po_arrived_mv) {
    state->window_count = 0U;
    return;
  }
  if (state->window_count &&
      fmaxf(state->max_mv, mv) - fminf(state->min_mv, mv) > state->config->po_stable_mv)
    state->window_count = 0U;
  if (!state->window_count) {
    state->window_start = tick;
    state->min_mv = state->max_mv = mv;
    state->power_sum = 0.0f;
  }
  state->min_mv = fminf(state->min_mv, mv);
  state->max_mv = fmaxf(state->max_mv, mv);
  state->power_sum += sample->vin_v * sample->iin_a;
  state->window_count++;
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
  // A timeout must not turn an unsettled sample into a power comparison.
  // 20 samples / 30 ms minimum window tolerates occasional missed millisecond slots.
  const bool averaged = config->po_average_ms != 0U;
  const bool settled = averaged ?
    (state->window_count >= 20U && now - state->observed_tick <= 3U &&
     state->observed_tick - state->window_start >= config->po_average_ms) :
    (arrived || stalled);
  state->status.flags = settled ? MPPT_SETTLED : 0U;
  state->status.window_samples = state->window_count;
  if (dwelled && sampled && settled) {
    state->last_po_ms = now;
    const float power = averaged ? state->power_sum / state->window_count :
      chan->telem.vin_v * chan->telem.iin_a;
    state->status.compared_target_mv = (uint32_t)state->vin_po.target;
    (void)po_update(&state->vin_po, power);
    state->status.decisions++;
    state->status.flags |= MPPT_UPDATED;
    state->window_count = 0U;
  }
  if (chan->telem.tick_ms != state->last_telem_tick) {
    uint32_t dt_ms = chan->telem.tick_ms - state->last_telem_tick;
    state->last_telem_tick = chan->telem.tick_ms;
    if (dt_ms > config->dt_max_ms) dt_ms = config->dt_max_ms;
    // Preserve sub-duty-unit integration unless the actuator missed our last command.
    if (!config->preserve_pi_fraction || chan->pwm.duty_applied != state->last_requested_duty) {
      pi_track(&state->vin_pi, (float)chan->pwm.duty_applied);
      state->status.flags |= MPPT_PI_TRACKING;
    }
    // Invert PI error: boost input voltage falls as duty rises.
    const uint16_t duty = (uint16_t)pi_update(&state->vin_pi, chan->telem.vin_v * 1000.0f,
                                             state->vin_po.target, (float)dt_ms);
    control_set_duty(channel, duty);
    state->last_requested_duty = duty;
  }
  state->status.target_mv = (uint32_t)state->vin_po.target;
  state->status.error_mv = (int32_t)(chan->telem.vin_v * 1000.0f - state->vin_po.target);
  state->status.power_w = state->vin_po.last_power_w;
  state->status.age_ms = now - state->last_po_ms;
  if (!state->vin_po.first_step) state->status.flags |= MPPT_POWER_VALID;
  if (state->vin_po.direction > 0) state->status.flags |= MPPT_DIRECTION_UP;
}

bool mppt_get_status(uint32_t channel, mppt_status_t *status) {
  const mppt_state_t *state = state_by_id(channel);
  if (!state || !state->config || !status) return false;
  *status = state->status;
  return true;
}
