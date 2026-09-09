#ifndef SYSTEM_H
#define SYSTEM_H

#include <stdbool.h>
#include <stdint.h>
#include "mode.h"

typedef enum {
  SYSTEM_STATE_INIT,
  SYSTEM_STATE_CHECK,
  SYSTEM_STATE_STANDBY,
  SYSTEM_STATE_ACTIVE,
  SYSTEM_STATE_FAULTED,
  SYSTEM_STATE_RESET
} system_state_t;

typedef struct
{
  uint32_t vbus_mv;
  volatile bool ovp_latched;
  system_state_t state;
  mode_t mode;
} system_t;

extern system_t sys;

void system_init(void);

#endif
