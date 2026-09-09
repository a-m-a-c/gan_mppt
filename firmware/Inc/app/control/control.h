#ifndef CONTROL_H
#define CONTROL_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  bool ramp_limit_enabled;
  bool thermal_limit_enabled;
  bool dynamic_frequency_enabled;
  bool dynamic_deadtime_enabled;
  bool dynamic_protection_ceilings_enabled;
  bool channel_a_enabled;
  bool channel_b_enabled;
  bool channel_c_enabled;
  bool channel_d_enabled;
  bool channel_e_enabled;

  uint32_t ramp_rate_per_ms; // Duty units per ms.
} control_config_t;

void control_init(control_config_t *config);

void control_service(void);

void control_set_duty(uint32_t channel, uint16_t duty_cycle);

void control_stop(void);

#endif
