#include "control.h"
#include "pwm.h"
#include "channel.h"
#include "main.h"

// Bench assumption: allow at most two 1 ms ramp intervals after a delayed pass.
#define RAMP_DT_MAX_MS 2U

static control_config_t *control_config;
static uint16_t requested_duty[CHANNEL_COUNT];
static const bool *channel_enabled[CHANNEL_COUNT];
static uint32_t last_duty_update_ms;

static void reset_requests(void) {
  for (uint32_t i = 0; i < CHANNEL_COUNT; i++) {
    requested_duty[i] = 0;
  }
  last_duty_update_ms = HAL_GetTick();
}

void control_init(control_config_t *config) {
  control_config = config;
  reset_requests();
  if (!config) return;
  channel_enabled[CHANNEL_A] = &config->channel_a_enabled;
  channel_enabled[CHANNEL_B] = &config->channel_b_enabled;
  channel_enabled[CHANNEL_C] = &config->channel_c_enabled;
  channel_enabled[CHANNEL_D] = &config->channel_d_enabled;
  channel_enabled[CHANNEL_E] = &config->channel_e_enabled;
}

void control_start(void) {
  if (!control_config) return;
  reset_requests();
  for (uint32_t i = 0; i < CHANNEL_COUNT; i++) {
    if (*channel_enabled[i] && !pwm_start(i)) {
      control_stop();
      return;
    }
  }
  last_duty_update_ms = HAL_GetTick();
}

static uint16_t apply_ramp_rate(uint16_t requested, uint16_t applied, uint32_t dt_ms) {
  const uint64_t step = (uint64_t)control_config->ramp_rate_per_ms * dt_ms;

  if (requested > applied) {
    const uint32_t distance = requested - applied;
    if (step < distance) return (uint16_t)(applied + step);
  } else {
    const uint32_t distance = applied - requested;
    if (step < distance) return (uint16_t)(applied - step);
  }
  return requested;
}

static void update_duty(channel_t *channel, uint16_t requested, uint32_t dt_ms) {
  if (channel->pwm.op_state != PWM_STATE_RUNNING) return;
  if (control_config->ramp_limit_enabled) {
    requested = apply_ramp_rate(requested, channel->pwm.duty_applied, dt_ms);
  }
  (void)pwm_set_duty_cycle(channel->id, requested);
}

void control_service(void) {
  if (!control_config) return;

  const uint32_t now = HAL_GetTick();
  uint32_t dt_ms = now - last_duty_update_ms;
  last_duty_update_ms = now;
  if (dt_ms > RAMP_DT_MAX_MS) dt_ms = RAMP_DT_MAX_MS;

  for (uint32_t i = 0; i < CHANNEL_COUNT; i++) {
    if (*channel_enabled[i]) update_duty(channel_by_id(i), requested_duty[i], dt_ms);
  }
}

void control_set_duty(uint32_t channel, uint16_t duty_cycle) {
  if (!control_config || channel >= CHANNEL_COUNT || duty_cycle > PWM_MAX_DUTY_CYCLE) return;
  if (*channel_enabled[channel]) requested_duty[channel] = duty_cycle;
}

void control_stop(void) {
  reset_requests();
  if (!control_config) return;
  for (uint32_t i = 0; i < CHANNEL_COUNT; i++) {
    if (*channel_enabled[i]) pwm_stop(i);
  }
}
