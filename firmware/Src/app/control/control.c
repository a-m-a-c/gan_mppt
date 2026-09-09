#include "control.h"
#include "pwm.h"

static control_config_t *control_config;
void control_init(control_config_t *config) {
  control_config = config;
}

void control_service(void) {
}

void control_set_duty(uint32_t channel, uint16_t duty_cycle) {
  pwm_set_duty_cycle(channel, duty_cycle);
}

void control_stop(void) {
  if (control_config->channel_a_enabled) pwm_stop(CHANNEL_A);
  if (control_config->channel_b_enabled) pwm_stop(CHANNEL_B);
  if (control_config->channel_c_enabled) pwm_stop(CHANNEL_C);
  if (control_config->channel_d_enabled) pwm_stop(CHANNEL_D);
  if (control_config->channel_e_enabled) pwm_stop(CHANNEL_E);
}
