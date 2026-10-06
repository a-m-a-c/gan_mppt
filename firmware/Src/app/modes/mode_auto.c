#include "mode_auto.h"

mode_request_result_t mode_auto_begin(void) {
  return MODE_INIT_OK;
}

mode_state_t mode_auto_service(bool stopping) {
  return stopping ? MODE_STATE_EXIT : MODE_STATE_RUNNING;
}
