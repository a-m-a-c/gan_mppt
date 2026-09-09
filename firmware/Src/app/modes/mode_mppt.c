#include "mode_mppt.h"

mode_request_result_t mode_mppt_begin(void) {
  // Not implemented.
  return MODE_INIT_OK;
}

mode_state_t mode_mppt_service(bool stopping) {
  return stopping ? MODE_STATE_EXIT : MODE_STATE_RUNNING;
}
