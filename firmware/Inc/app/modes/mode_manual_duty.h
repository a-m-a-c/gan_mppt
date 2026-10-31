#ifndef MODE_MANUAL_DUTY_H
#define MODE_MANUAL_DUTY_H

#include "mode.h"
#include <stdint.h>

typedef enum {
  MANUAL_FAULT_NONE,
  MANUAL_FAULT_HARDWARE_OVP,
  MANUAL_FAULT_HARDWARE_OCP_A,
  MANUAL_FAULT_HARDWARE_OTHER,
  MANUAL_FAULT_BUS_LOW,
  MANUAL_FAULT_BUS_HIGH,
  MANUAL_FAULT_TELEM_INVALID,
  MANUAL_FAULT_TELEM_STALE,
  MANUAL_FAULT_TELEM_NONFINITE,
  MANUAL_FAULT_INPUT_LOW,
  MANUAL_FAULT_INPUT_HIGH,
  MANUAL_FAULT_INPUT_REVERSE,
  MANUAL_FAULT_INPUT_CURRENT,
  MANUAL_FAULT_OUTPUT_REVERSE,
  MANUAL_FAULT_OUTPUT_CURRENT,
  MANUAL_FAULT_OUTPUT_VOLTAGE,
  MANUAL_FAULT_OUTPUT_HEADROOM,
  MANUAL_FAULT_PWM_STATE,
  MANUAL_FAULT_PWM_DUTY,
  MANUAL_FAULT_PWM_WRITE,
  MANUAL_FAULT_STARTUP_CURRENT,
  MANUAL_FAULT_STARTUP_HEADROOM
} manual_fault_code_t;

typedef struct {
  manual_fault_code_t code;
  uint16_t duty;
  int32_t value; // mV, mA, ms, or duty units according to code; zero for hardware faults.
} manual_fault_t;

mode_request_result_t mode_manual_duty_begin(void);
bool mode_manual_duty_set(uint16_t duty);
mode_state_t mode_manual_duty_service(bool stopping);
void mode_manual_duty_end(void);
// UINT16_MAX means inactive; otherwise this is the accepted target.
uint16_t mode_manual_duty_target(void);
const manual_fault_t *mode_manual_duty_fault(void);

#endif
