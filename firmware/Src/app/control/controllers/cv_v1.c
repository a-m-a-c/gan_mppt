#include "cv_v1.h"
#include "channel.h"
#include "control.h"
#include "main.h"
#include "system.h"
#include "pi.h"
#include <stddef.h>

typedef struct {
  const cv_config_t *config;
  uint32_t last_sample_ms;
  pi_t voltage_pi;
} cv_state_t;

static cv_state_t cv_a;
static cv_state_t cv_b;
static cv_state_t cv_c;
static cv_state_t cv_d;
static cv_state_t cv_e;

static cv_state_t *state_by_id(uint32_t channel) {
  switch (channel) {
    case CHANNEL_A: return &cv_a;
    case CHANNEL_B: return &cv_b;
    case CHANNEL_C: return &cv_c;
    case CHANNEL_D: return &cv_d;
    case CHANNEL_E: return &cv_e;
    default: return NULL;
  }
}

void cv_begin(uint32_t channel, const cv_config_t *config) {
  cv_state_t *state = state_by_id(channel);
  if (!state) return;
  state->config = config;
  if (!config) return;
  state->last_sample_ms = HAL_GetTick();
  pi_init(&state->voltage_pi, config->kp, config->ki, config->duty_min, config->duty_max);
}

void cv_service(uint32_t channel) {
  cv_state_t *state = state_by_id(channel);
  if (!state || !state->config) return;
  const channel_t *chan = channel_by_id(channel);
  if (chan->pwm.op_state != PWM_STATE_RUNNING) return;
  const cv_config_t *config = state->config;
  const uint32_t now = HAL_GetTick();
  uint32_t dt_ms = now - state->last_sample_ms;
  if (dt_ms < config->sample_time_ms) return;
  state->last_sample_ms = now;
  if (dt_ms > config->dt_max_ms) dt_ms = config->dt_max_ms;
  pi_track(&state->voltage_pi, (float)chan->pwm.duty_applied);
  const uint16_t duty = (uint16_t)pi_update(&state->voltage_pi, (float)config->target_mv,
                                           (float)sys.vbus_mv, (float)dt_ms);
  control_set_duty(channel, duty);
}
