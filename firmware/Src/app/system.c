#include "system.h"

system_t sys;

void system_init(void) {
  sys.vbus_mv = 0U;
  sys.ovp_latched = false;
  sys.state = SYSTEM_STATE_INIT;
  sys.mode = MODE_NONE;
}
