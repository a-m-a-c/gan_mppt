#ifndef MPPT_V1_H
#define MPPT_V1_H

#include <stdint.h>
#include <stdbool.h>

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
  // Zero keeps the original single-sample policy for other modes.
  uint32_t po_average_ms;
  uint32_t po_stable_mv;
  bool preserve_pi_fraction;
} mppt_config_t;

enum {
  MPPT_UPDATED = 1U, MPPT_POWER_VALID = 2U, MPPT_SETTLED = 4U,
  MPPT_PI_TRACKING = 8U, MPPT_DIRECTION_UP = 16U,
};
typedef struct mppt_status {
  uint32_t target_mv;
  int32_t error_mv; // Measured Vin minus current target (boost PI convention).
  float power_w; // Last accepted P&O power; valid only with MPPT_POWER_VALID.
  uint32_t age_ms;
  uint32_t decisions;
  uint32_t window_samples;
  uint16_t flags;
  uint32_t compared_target_mv; // Target at which the last accepted power was measured.
} mppt_status_t;

// Config must remain alive until the next begin for this channel.
void mppt_begin(uint32_t channel, const mppt_config_t *config);
// Call between control updates; accepts at most one fresh sample per millisecond.
void mppt_observe(uint32_t channel);
void mppt_service(uint32_t channel);
bool mppt_get_status(uint32_t channel, mppt_status_t *status);

#endif
