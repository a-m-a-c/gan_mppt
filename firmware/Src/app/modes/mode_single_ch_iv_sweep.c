#include "mode_single_ch_iv_sweep.h"
#include "main.h"
#include "system.h"
#include "channel.h"
#include "pwm.h"
#include <stdint.h>

#define MAX_DUTY_CYCLE 700U

#define DUTY_CYCLE_STEP 5U

#define DUTY_STEP_INTERVAL_MS 100U

#define TELEM_MAX_AGE_MS 120U

// Bench load: sqrt(44.3 W * 2.5 ohm) = 10.5 V; 15 V indicates load loss.
#define MAX_OUTPUT_MV 15000U

#define IIN_IDLE_MAX_A 1.0f

#define SWEEP_CYCLES 10U

static uint32_t last_duty_step_time_ms;
static uint16_t cycles_done;
static bool descending;

static bool telem_is_fresh(uint32_t now) {
  return channel_a.telem.valid && ((now - channel_a.telem.tick_ms) <= TELEM_MAX_AGE_MS);
}

mode_request_result_t mode_single_ch_iv_sweep_begin(void) {
  const uint32_t now = HAL_GetTick();

  if (!telem_is_fresh(now)) return MODE_INIT_REFUSED;

  last_duty_step_time_ms = now;
  cycles_done = 0;
  descending = false;

  if (!pwm_start(CHANNEL_A)) return MODE_INIT_REFUSED;

  return MODE_INIT_OK;
}

mode_state_t mode_single_ch_iv_sweep_service(bool stopping) {
  const uint32_t now = HAL_GetTick();

  if (sys.ovp_latched || channel_a.pwm.ocp_latched) {
    pwm_stop(CHANNEL_A);
    return MODE_STATE_FAULTED;
  }

  if (stopping) {
    pwm_stop(CHANNEL_A);
    return MODE_STATE_EXIT;
  }

  if (!telem_is_fresh(now)) {
    pwm_stop(CHANNEL_A);
    return MODE_STATE_FAULTED;
  }

  // Use the 1 ms ADC reading for load-loss protection; INA228 updates every 40 ms.

  if (sys.vbus_mv >= MAX_OUTPUT_MV) {
    pwm_stop(CHANNEL_A);
    return MODE_STATE_FAULTED;
  }

  const bool dwelled = (now - last_duty_step_time_ms) >= DUTY_STEP_INTERVAL_MS;
  const bool sampled = (int32_t)(channel_a.telem.tick_ms - last_duty_step_time_ms) > 0;

  if (dwelled && sampled) {
    const uint16_t duty = channel_a.pwm.duty_applied;

    // Dwell at the endpoints before reversing so both get settled samples.
    if (!descending && (duty >= MAX_DUTY_CYCLE)) {
      descending = true;
    } else if (descending && (duty == 0U)) {
      descending = false;
      cycles_done++;
      if (cycles_done >= SWEEP_CYCLES) {
        pwm_stop(CHANNEL_A);
        return MODE_STATE_EXIT;
      }
    }

    uint16_t new_duty;
    if (descending) {
      new_duty = (duty > DUTY_CYCLE_STEP) ? (uint16_t)(duty - DUTY_CYCLE_STEP) : 0U;
    } else {
      new_duty = (uint16_t)(duty + DUTY_CYCLE_STEP);
      if (new_duty > MAX_DUTY_CYCLE) new_duty = MAX_DUTY_CYCLE;
    }

    if (!pwm_set_duty_cycle(CHANNEL_A, new_duty)) {
      pwm_stop(CHANNEL_A);
      return MODE_STATE_FAULTED;
    }
    last_duty_step_time_ms = now;
  }

  return MODE_STATE_RUNNING;
}
