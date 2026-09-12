#include "fault.h"
#include "pwm.h"

void fault_service(system_state_t prev_state) {
  if (sys.state == SYSTEM_STATE_RESET) return;

  if (pwm_faults_present()) {
    sys.state = SYSTEM_STATE_FAULTED;
  }
  if (sys.state == SYSTEM_STATE_FAULTED && prev_state != SYSTEM_STATE_FAULTED) {
    pwm_stop_all();
    sys.mode = MODE_NONE;
  }
}
