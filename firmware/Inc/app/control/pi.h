#ifndef PI_H
#define PI_H

#include <stdbool.h>

typedef struct {
  float kp;
  float ki;
  float out_min;
  float out_max;

  float integral;
  float last_error;
  bool clamped;
} pi_t;

void pi_init(pi_t *pi, float kp, float ki, float out_min, float out_max);

void pi_reset(pi_t *pi);

float pi_update(pi_t *pi, float setpoint, float measurement, float dt_ms);

void pi_track(pi_t *pi, float applied);

#endif
