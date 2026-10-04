#include "mode_single_ch_cv.h"
#include "main.h"
#include "system.h"
#include "channel.h"
#include "pi.h"
#include "control.h"
#include <stdint.h>

// Bench mode: do not run into a battery.

control_config_t control_cfg = {
  .ramp_limit_enabled = true,
  .thermal_limit_enabled = false,
  .dynamic_frequency_enabled = false,
  .dynamic_deadtime_enabled = false,
  .dynamic_protection_ceilings_enabled = false,
  .channel_a_enabled = true,
  .channel_b_enabled = false,
  .channel_c_enabled = false,
  .channel_d_enabled = false,
  .channel_e_enabled = false,
  .ramp_rate_per_ms = 15,
};


#define VOUT_MV 25000
#define KP 0.006f
#define KI 0.6f
#define SAMPLE_TIME_MS 1
#define DT_MAX_MS (2U * SAMPLE_TIME_MS)
#define MAX_DUTY_CYCLE 750.0f
#define MIN_DUTY_CYCLE 0.0f

static uint32_t last_sample_ms;
static pi_t volt_pi;

mode_request_result_t mode_single_ch_cv_begin(void) {
  last_sample_ms = HAL_GetTick();
  pi_init(&volt_pi, KP, KI, MIN_DUTY_CYCLE, MAX_DUTY_CYCLE);
  control_init(&control_cfg);
  control_start();
  return MODE_INIT_OK;
}

mode_state_t mode_single_ch_cv_service(bool stopping) {
  const uint32_t now = HAL_GetTick();

  if (stopping) {
    control_stop();
    return MODE_STATE_EXIT;
  }

  uint32_t dt_ms = now - last_sample_ms;
  if (dt_ms > DT_MAX_MS) {
    dt_ms = DT_MAX_MS;
  }

  if (dt_ms >= SAMPLE_TIME_MS) {
    last_sample_ms = now;

    pi_track(&volt_pi, (float)channel_a.pwm.duty_applied);
    uint16_t duty = (uint16_t)pi_update(&volt_pi, (float)VOUT_MV, (float)sys.vbus_mv, (float)dt_ms);
    control_set_duty(CHANNEL_A, duty);
  }

  control_service();
  return MODE_STATE_RUNNING;
}
