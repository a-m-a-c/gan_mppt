#include "mode_manual_duty.h"
#include "channel.h"
#include "diode_bypass.h"
#include "main.h"
#include "pwm.h"
#include "system.h"
#include <math.h>

// Same channel A / 39-cell panel / 12S4P bench limits as the measurement sweep.
#define BUS_MIN_MV 35000U
#define BUS_MAX_MV 50000U // 50.4 V pack maximum minus 0.4 V margin.
#define INPUT_MIN_V 10.0f
#define INPUT_MAX_V 35.0f
#define INPUT_MAX_A 9.5f // About 1.10 * reference-panel Isc (8.66 A).
#define OUTPUT_MAX_A 8.0f // 80% of the pack's 10 A charge limit.
#define REVERSE_MAX_A 10.0f // Requested bench threshold, not a validated reverse-current rating.
#define IDLE_MAX_A 0.5f
#define TELEM_MAX_AGE_MS 120U // Three nominal 40 ms normal-telemetry intervals.
#define STEP_PERIOD_MS 100U // One duty unit / 100 ms = 1 percentage point/s.

static bool active;
static uint16_t target, applied;
static uint32_t last_step_ms;
static manual_fault_t last_fault;

const manual_fault_t *mode_manual_duty_fault(void) {
  return &last_fault;
}

static bool trip(manual_fault_code_t code, int32_t value) {
  // A fault IRQ can arrive after hardware_ok() but before the PWM-state read.
  // Replace that generic consequence with the hardware cause, preserving duty.
  const bool hardware_cause = code >= MANUAL_FAULT_HARDWARE_OVP && code <= MANUAL_FAULT_HARDWARE_OTHER;
  if (last_fault.code == MANUAL_FAULT_NONE ||
      (last_fault.code == MANUAL_FAULT_PWM_STATE && hardware_cause))
    last_fault = (manual_fault_t){.code = code, .duty = applied, .value = value};
  return false;
}

static bool hardware_ok(void) {
  if (!pwm_faults_present()) return true;
  return trip(sys.ovp_latched ? MANUAL_FAULT_HARDWARE_OVP :
    channel_a.pwm.ocp_latched ? MANUAL_FAULT_HARDWARE_OCP_A : MANUAL_FAULT_HARDWARE_OTHER, 0);
}

static bool readings_ok(uint32_t now) {
  const chan_telem_t *t = &channel_a.telem;
  if (sys.vbus_mv < BUS_MIN_MV) return trip(MANUAL_FAULT_BUS_LOW, sys.vbus_mv);
  if (sys.vbus_mv >= BUS_MAX_MV) return trip(MANUAL_FAULT_BUS_HIGH, sys.vbus_mv);
  if (!t->valid) return trip(MANUAL_FAULT_TELEM_INVALID, 0);
  if (now - t->tick_ms > TELEM_MAX_AGE_MS)
    return trip(MANUAL_FAULT_TELEM_STALE, now - t->tick_ms);
  if (!isfinite(t->vin_v) || !isfinite(t->iin_a) ||
      !isfinite(t->vout_v) || !isfinite(t->iout_a))
    return trip(MANUAL_FAULT_TELEM_NONFINITE, 0);
  if (t->vin_v < INPUT_MIN_V) return trip(MANUAL_FAULT_INPUT_LOW, t->vin_v * 1000.0f);
  if (t->vin_v >= INPUT_MAX_V) return trip(MANUAL_FAULT_INPUT_HIGH, t->vin_v * 1000.0f);
  if (t->iin_a <= -REVERSE_MAX_A) return trip(MANUAL_FAULT_INPUT_REVERSE, t->iin_a * 1000.0f);
  if (t->iin_a >= INPUT_MAX_A) return trip(MANUAL_FAULT_INPUT_CURRENT, t->iin_a * 1000.0f);
  if (t->iout_a <= -REVERSE_MAX_A) return trip(MANUAL_FAULT_OUTPUT_REVERSE, t->iout_a * 1000.0f);
  if (t->iout_a >= OUTPUT_MAX_A) return trip(MANUAL_FAULT_OUTPUT_CURRENT, t->iout_a * 1000.0f);
  if (t->vout_v < 0.0f || t->vout_v >= BUS_MAX_MV / 1000.0f)
    return trip(MANUAL_FAULT_OUTPUT_VOLTAGE, t->vout_v * 1000.0f);
  if (t->vout_v >= sys.vbus_mv / 1000.0f + 1.0f)
    return trip(MANUAL_FAULT_OUTPUT_HEADROOM, t->vout_v * 1000.0f);
  return true;
}

void mode_manual_duty_end(void) {
  // app_loop can detect a hardware fault before this mode is serviced.
  // Keep the tracked duty: pwm_stop_all() may already have reset the driver duty.
  if (active) (void)hardware_ok();
  active = false;
  target = applied = 0U;
  pwm_stop_all();
  enable_bypass(false);
}

uint16_t mode_manual_duty_target(void) {
  return active && sys.state == SYSTEM_STATE_ACTIVE && sys.mode == MODE_MANUAL_DUTY
    ? target : UINT16_MAX;
}

bool mode_manual_duty_set(uint16_t duty) {
  if (mode_manual_duty_target() == UINT16_MAX || duty > PWM_MAX_DUTY_CYCLE)
    return false;
  target = duty;
  return true;
}

mode_request_result_t mode_manual_duty_begin(void) {
  mode_manual_duty_end();
  last_fault = (manual_fault_t){0};
  const uint32_t now = HAL_GetTick();
  if (!hardware_ok()) return MODE_INIT_FAULT;
  if (!readings_ok(now)) return MODE_INIT_REFUSED;
  if (fabsf(channel_a.telem.iin_a) > IDLE_MAX_A || fabsf(channel_a.telem.iout_a) > IDLE_MAX_A) {
    (void)trip(MANUAL_FAULT_STARTUP_CURRENT,
      (fabsf(channel_a.telem.iin_a) > IDLE_MAX_A ? channel_a.telem.iin_a : channel_a.telem.iout_a) * 1000.0f);
    return MODE_INIT_REFUSED;
  }
  if (sys.vbus_mv < channel_a.telem.vin_v * 1000.0f + 2000.0f) {
    (void)trip(MANUAL_FAULT_STARTUP_HEADROOM, sys.vbus_mv - (int32_t)(channel_a.telem.vin_v * 1000.0f));
    return MODE_INIT_REFUSED;
  }
  if (!pwm_start(CHANNEL_A, 0U)) {
    if (hardware_ok()) (void)trip(MANUAL_FAULT_PWM_WRITE, 0);
    mode_manual_duty_end();
    return MODE_INIT_FAULT;
  }
  active = true;
  last_step_ms = now;
  return MODE_INIT_OK;
}

mode_state_t mode_manual_duty_service(bool stopping) {
  const uint32_t now = HAL_GetTick();
  enable_bypass(false);
  if (stopping) {
    mode_manual_duty_end();
    return MODE_STATE_EXIT;
  }
  if (!active || !hardware_ok() || !readings_ok(now)) {
    if (last_fault.code == MANUAL_FAULT_NONE) (void)trip(MANUAL_FAULT_PWM_STATE, channel_a.pwm.op_state);
    mode_manual_duty_end();
    return MODE_STATE_FAULTED;
  }
  if (channel_a.pwm.op_state != PWM_STATE_RUNNING || channel_a.pwm.duty_applied != applied ||
      applied > PWM_MAX_DUTY_CYCLE) {
    if (hardware_ok())
      (void)trip(channel_a.pwm.op_state != PWM_STATE_RUNNING ? MANUAL_FAULT_PWM_STATE : MANUAL_FAULT_PWM_DUTY,
        channel_a.pwm.op_state != PWM_STATE_RUNNING ? (int32_t)channel_a.pwm.op_state : channel_a.pwm.duty_applied);
    mode_manual_duty_end();
    return MODE_STATE_FAULTED;
  }
  if (target != applied && now - last_step_ms >= STEP_PERIOD_MS) {
    const uint16_t next = target > applied ? applied + 1U : applied - 1U;
    if (!pwm_set_duty_cycle(CHANNEL_A, next)) {
      if (hardware_ok()) (void)trip(MANUAL_FAULT_PWM_WRITE, next);
      mode_manual_duty_end();
      return MODE_STATE_FAULTED;
    }
    applied = next;
    last_step_ms = now; // No catch-up steps after a delayed pass.
  }
  return MODE_STATE_RUNNING;
}
