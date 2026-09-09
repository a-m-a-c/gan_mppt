#include "pi.h"

void pi_init(pi_t *pi, float kp, float ki, float out_min, float out_max) {
  pi->kp = kp;
  pi->ki = ki;
  pi->out_min = out_min;
  pi->out_max = out_max;
  pi_reset(pi);
}

void pi_reset(pi_t *pi) {
  pi->integral = 0.0f;
  pi->last_error = 0.0f;
  pi->clamped = false;
}

float pi_update(pi_t *pi, float setpoint, float measurement, float dt_ms) {
  float error = setpoint - measurement;
  float p_term = pi->kp * error;
  float i_term = pi->integral + pi->ki * error * (dt_ms / 1000.0f);
  float output = p_term + i_term;

  if (output > pi->out_max) {
    output = pi->out_max;
    pi->clamped = true;
  } else if (output < pi->out_min) {
    output = pi->out_min;
    pi->clamped = true;
  } else {
    pi->integral = i_term;
    pi->clamped = false;
  }
  pi->last_error = error;
  return output;
}

void pi_track(pi_t *pi, float applied) {
  pi->integral = applied - (pi->kp * pi->last_error);
}
