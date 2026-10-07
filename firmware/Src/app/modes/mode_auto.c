#include "mode_auto.h"
#include "channel.h"
#include "control.h"
#define START_DELAY_MS 2000

// Control gate config
control_config_t control_cfg = {
  .ramp_limit_enabled = true,
  .thermal_limit_enabled = false,
  .dynamic_frequency_enabled = false,
  .dynamic_deadtime_enabled = false,
  .dynamic_protection_ceilings_enabled = false,
  .channel_a_enabled = true,
  .channel_b_enabled = true,
  .channel_c_enabled = true,
  .channel_d_enabled = true,
  .channel_e_enabled = true,
  .ramp_rate_per_ms = 1,
};

// Channel state machine to track plugging and starting.
typedef enum {
  AUTO_CHANNEL_UNPLUGGED = 0,
  AUTO_CHANNEL_PENDING_START,
  AUTO_CHANNEL_ON,
} auto_channel_state_t;

typedef struct {
  auto_channel_state_t state;
  uint32_t plug_time_ms;
} auto_channel_t;

static auto_channel_t auto_a;
static auto_channel_t auto_b;
static auto_channel_t auto_c;
static auto_channel_t auto_d;
static auto_channel_t auto_e;

// FSM to deal with channel state transition logic, main logic done in main loop based on state.
static void channel_fsm_service(auto_channel_t *auto_chan, channel_t *chan) {
  switch (auto_chan->state) {
    case AUTO_CHANNEL_UNPLUGGED:
      if (chan->telem.vin_v > 5.0) {
        auto_chan->state = AUTO_CHANNEL_PENDING_START;
        auto_chan->plug_time_ms = chan->telem.tick_ms;
      }
      break;
    case AUTO_CHANNEL_PENDING_START:
      if (chan->telem.vin_v <= 5.0f) {
        auto_chan->state = AUTO_CHANNEL_UNPLUGGED;
        auto_chan->plug_time_ms = 0;
      } else if (chan->telem.tick_ms - auto_chan->plug_time_ms >= START_DELAY_MS) {
        control_start(chan->id);
        if (chan->pwm.op_state == PWM_STATE_RUNNING) {
          auto_chan->state = AUTO_CHANNEL_ON;
        } else {
          auto_chan->state = AUTO_CHANNEL_UNPLUGGED;
          auto_chan->plug_time_ms = 0;
        }
      }
      break;
    case AUTO_CHANNEL_ON:
      if (chan->telem.vin_v < 4.0) {
        auto_chan->state = AUTO_CHANNEL_UNPLUGGED;
        auto_chan->plug_time_ms = 0;
        control_stop(chan->id);
      }
      break;
  }
}

mode_request_result_t mode_auto_begin(void) {
  control_init(&control_cfg);
  auto_a = (auto_channel_t){ .state = AUTO_CHANNEL_UNPLUGGED, .plug_time_ms = 0 };
  auto_b = (auto_channel_t){ .state = AUTO_CHANNEL_UNPLUGGED, .plug_time_ms = 0 };
  auto_c = (auto_channel_t){ .state = AUTO_CHANNEL_UNPLUGGED, .plug_time_ms = 0 };
  auto_d = (auto_channel_t){ .state = AUTO_CHANNEL_UNPLUGGED, .plug_time_ms = 0 };
  auto_e = (auto_channel_t){ .state = AUTO_CHANNEL_UNPLUGGED, .plug_time_ms = 0 };
  return MODE_INIT_OK;
}

mode_state_t mode_auto_service(bool stopping) {
  if (stopping) {
    control_stop_all();
    return MODE_STATE_EXIT;
  }
  // Check what is plugged in and update the status.
  // Check the status and determine if we should turn on, off, or stay in current state.
  channel_fsm_service(&auto_a, &channel_a);
  channel_fsm_service(&auto_b, &channel_b);
  channel_fsm_service(&auto_c, &channel_c);
  channel_fsm_service(&auto_d, &channel_d);
  channel_fsm_service(&auto_e, &channel_e);
  // Check bus voltage, determine if we should transition to CV.
  // todo
  for (int i = CHANNEL_A; i <= CHANNEL_E; i++) {

  }
  // MPPT control logic and CV control logic.
  return MODE_STATE_RUNNING;
}
