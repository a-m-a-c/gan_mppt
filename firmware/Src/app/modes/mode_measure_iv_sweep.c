#include "mode_measure_iv_sweep.h"
#include "channel.h"
#include "diode_bypass.h"
#include "main.h"
#include "measure_io.h"
#include "pwm.h"
#include "system.h"
#include <math.h>

// Channel A, 39-cell panel, 12S4P 10 Ah battery. See .agents/hardware.md.
#define BUS_MIN_MV 35000U
#define BUS_MAX_MV 50000U
#define INPUT_MIN_V 10.0f
#define INPUT_MAX_V 35.0f
#define INPUT_MAX_A 9.5f
#define OUTPUT_MAX_A 8.0f
#define IDLE_MAX_A 0.5f // Preserve the PWM-off startup check separately.
#define OUTPUT_BUS_HEADROOM_V 1.0f
#define STOP_DUTY 700U // Hold 70.0% for one step, then stop PWM; no return ramp.
#define SWEEP_FREQUENCY_HZ 600000U
_Static_assert(SWEEP_FREQUENCY_HZ >= PWM_MIN_FREQUENCY_HZ &&
               SWEEP_FREQUENCY_HZ <= PWM_MAX_FREQUENCY_HZ, "IV sweep frequency exceeds PWM limits");
#define BASELINE_MS 2000U
#define DUTY_STEP 1U // One unit = 0.1 percentage point.
#define STEP_PERIOD_MS 100U // 0.1% / 100 ms = 1 percentage point/s.
#define POST_MS 500U
#define ACQUIRE_TIMEOUT_MS 2000U
#define STALE_MS 10U
// 2 + 700 * 0.1 + 0.1 + 0.5 = 72.6 s; 85 s allows setup/loop margin.
#define RUN_TIMEOUT_MS 85000U
#define IV_FLAGS (MEASURE_IV_SWEEP | MEASURE_IV_DUTY_RAMP)

typedef enum { IV_WAIT, IV_BASELINE, IV_RAMP, IV_POST } iv_phase_t;
static iv_phase_t phase;
static uint32_t start_ms, phase_ms, last_step_ms;
static uint16_t duty;
static uint16_t terminal_flags;
static bool frequency_owned;
static uint32_t previous_frequency_hz;

void mode_measure_iv_sweep_end(void) {
  pwm_stop_all();
  enable_bypass(false);
  if (frequency_owned) {
    pwm_set_frequency(CHANNEL_A, previous_frequency_hz);
    frequency_owned = false;
  }
  if (measure_io_owned()) measure_io_end();
}

static mode_state_t finish(bool fault) {
  mode_measure_iv_sweep_end();
  return fault ? MODE_STATE_FAULTED : MODE_STATE_EXIT;
}

static bool bus_ok(void) {
  return sys.vbus_mv >= BUS_MIN_MV && sys.vbus_mv < BUS_MAX_MV;
}

static bool readings_ok(uint32_t now) {
  const chan_telem_t *t = &channel_a.telem;
  return t->valid && now - t->tick_ms <= STALE_MS &&
    isfinite(t->vin_v) && isfinite(t->iin_a) &&
    isfinite(t->vout_v) && isfinite(t->iout_a) &&
    t->vin_v >= INPUT_MIN_V && t->vin_v < INPUT_MAX_V &&
    // Reverse current is permitted in this bench sweep at the user's request.
    t->iin_a < INPUT_MAX_A && t->iout_a < OUTPUT_MAX_A &&
    t->vout_v >= 0.0f && t->vout_v < BUS_MAX_MV / 1000.0f &&
    t->vout_v < sys.vbus_mv / 1000.0f + OUTPUT_BUS_HEADROOM_V;
}

mode_request_result_t mode_measure_iv_sweep_begin(void) {
  pwm_stop_all();
  enable_bypass(false);
  if (pwm_faults_present()) return MODE_INIT_FAULT;
  if (!bus_ok() || !measure_io_begin(CHANNEL_A)) return MODE_INIT_REFUSED;
  previous_frequency_hz = channel_a.pwm.frequency_hz;
  frequency_owned = true;
  measure_io_set_flags(IV_FLAGS);
  pwm_set_frequency(CHANNEL_A, SWEEP_FREQUENCY_HZ);
  if (channel_a.pwm.frequency_hz != SWEEP_FREQUENCY_HZ) {
    mode_measure_iv_sweep_end();
    return MODE_INIT_FAULT;
  }
  phase = IV_WAIT;
  start_ms = phase_ms = HAL_GetTick();
  terminal_flags = 0U;
  duty = 0U;
  return MODE_INIT_OK;
}

mode_state_t mode_measure_iv_sweep_service(bool stopping) {
  const uint32_t now = HAL_GetTick();
  enable_bypass(false);
  if (stopping) return finish(false);
  if (pwm_faults_present() || measure_io_failed() || !bus_ok() ||
      now - start_ms >= RUN_TIMEOUT_MS) return finish(true);

  if (phase == IV_WAIT) {
    if (!measure_io_ready()) {
      if (now - start_ms >= ACQUIRE_TIMEOUT_MS) return finish(true);
      return MODE_STATE_RUNNING;
    }
    phase = IV_BASELINE;
    phase_ms = now;
  }
  if (!measure_io_ready() || !readings_ok(now)) return finish(true);

  if (phase == IV_BASELINE) {
    if (fabsf(channel_a.telem.iin_a) > IDLE_MAX_A ||
        fabsf(channel_a.telem.iout_a) > IDLE_MAX_A ||
        sys.vbus_mv < channel_a.telem.vin_v * 1000.0f + 2000.0f)
      return finish(true);
    if (now - phase_ms < BASELINE_MS) return MODE_STATE_RUNNING;
    if (!pwm_start(CHANNEL_A, 0U)) return finish(true);
    phase = IV_RAMP;
    last_step_ms = now;
  }

  if (phase == IV_POST) {
    if (now - phase_ms >= POST_MS) {
      measure_io_set_flags(IV_FLAGS | terminal_flags);
      return finish(false);
    }
    return MODE_STATE_RUNNING;
  }

  if (channel_a.pwm.op_state != PWM_STATE_RUNNING ||
      channel_a.pwm.duty_applied != duty || duty > STOP_DUTY ||
      channel_a.pwm.frequency_hz != SWEEP_FREQUENCY_HZ) return finish(true);
  if (duty == STOP_DUTY && now - last_step_ms >= STEP_PERIOD_MS) {
    terminal_flags = MEASURE_IV_COMPLETE;
    pwm_stop_all();
    phase = IV_POST;
    phase_ms = now;
    return MODE_STATE_RUNNING;
  }
  if (now - last_step_ms >= STEP_PERIOD_MS) {
    // No catch-up steps after a delayed pass: always hold the next step for 100 ms.
    const uint16_t next = duty + DUTY_STEP;
    if (!pwm_set_duty_cycle(CHANNEL_A, next)) return finish(true);
    duty = next;
    last_step_ms = now;
  }
  return MODE_STATE_RUNNING;
}
