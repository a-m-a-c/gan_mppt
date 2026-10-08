#ifndef MPPT_V1_H
#define MPPT_V1_H

#include <stdint.h>

typedef struct {
  float kp;
  float ki;
  uint16_t duty_min;
  uint16_t duty_max;
  uint32_t po_period_ms;
  uint32_t po_step_mv;
  uint32_t po_arrived_mv;
  uint32_t po_stall_ms;
  float seed_fraction;
  uint32_t target_min_mv;
  uint32_t target_max_mv;
  uint32_t dt_max_ms;
} mppt_config_t;

// Config must remain alive until the next begin for this channel.
void mppt_begin(uint32_t channel, const mppt_config_t *config);
void mppt_service(uint32_t channel);

#endif
