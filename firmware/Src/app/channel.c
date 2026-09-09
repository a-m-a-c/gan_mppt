#include "channel.h"

#include <stddef.h>

#include "config.h"

channel_t channel_a;
channel_t channel_b;
channel_t channel_c;
channel_t channel_d;
channel_t channel_e;

channel_t *channel_by_id(uint32_t id) {
  switch (id) {
    case CHANNEL_A: return &channel_a;
    case CHANNEL_B: return &channel_b;
    case CHANNEL_C: return &channel_c;
    case CHANNEL_D: return &channel_d;
    case CHANNEL_E: return &channel_e;
    default: break;
  }
  return NULL;
}

void channel_init_all(void) {
  for (uint32_t i = 0U; i < CHANNEL_COUNT; i++) {
    channel_t *ch = channel_by_id(i);

    ch->id = i;

    ch->telem.vin_v = 0.0f;
    ch->telem.iin_a = 0.0f;
    ch->telem.vout_v = 0.0f;
    ch->telem.iout_a = 0.0f;
    ch->telem.tick_ms = 0U;
    ch->telem.valid = false;

    ch->pwm.frequency_hz = PWM_DEFAULT_FREQUENCY_HZ;
    ch->pwm.duty_applied = PWM_DEFAULT_DUTY_CYCLE;
    ch->pwm.dead_time_ns = PWM_DEFAULT_DEAD_TIME_NS;
    ch->pwm.op_state = PWM_STATE_UNINITIALIZED;
    ch->pwm.ocp_latched = false;
  }
}
