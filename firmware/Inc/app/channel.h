#ifndef CHANNEL_H
#define CHANNEL_H

#include <stdbool.h>
#include <stdint.h>

#include "config.h"

typedef enum {
  PWM_STATE_UNINITIALIZED = 0,
  PWM_STATE_STOPPED,
  PWM_STATE_RUNNING,
  PWM_STATE_FAULTED
} pwm_state_t;

typedef struct
{
  float vin_v;
  float iin_a;
  float vout_v;
  float iout_a;
  uint32_t tick_ms;
  bool valid;
} chan_telem_t;

typedef struct
{
  uint32_t frequency_hz;
  uint16_t duty_applied;
  uint16_t dead_time_ns;
  pwm_state_t op_state;
  volatile bool ocp_latched;
} chan_pwm_t;

typedef struct
{
  uint32_t id;
  chan_telem_t telem;
  chan_pwm_t pwm;
} channel_t;

extern channel_t channel_a;
extern channel_t channel_b;
extern channel_t channel_c;
extern channel_t channel_d;
extern channel_t channel_e;

void channel_init_all(void);

channel_t *channel_by_id(uint32_t id);

#endif
