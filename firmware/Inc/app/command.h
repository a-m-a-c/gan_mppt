#ifndef COMMAND_H
#define COMMAND_H

#include <stdint.h>
#include <stdbool.h>
#include "mode.h"

typedef enum commands {
    SYSTEM_COMMAND_NONE,
    SYSTEM_COMMAND_RESET,
    SYSTEM_COMMAND_CLEAR_FAULT,
    SYSTEM_COMMAND_STOP,
    SYSTEM_COMMAND_RUN_AUTO,
    SYSTEM_COMMAND_RUN_SINGLE_CH_MPPT,
    SYSTEM_COMMAND_RUN_SINGLE_CH_IV_SWEEP,
    SYSTEM_COMMAND_BYPASS_ON,
    SYSTEM_COMMAND_BYPASS_OFF,
    SYSTEM_COMMAND_RUN_DUAL_CH_MPPT,
    SYSTEM_COMMAND_RUN_SINGLE_CH_5_MPPT
} system_commands_t;

void command_init(void);

mode_t system_command_requested_mode(void);

bool system_command_received(system_commands_t command);

void command_service(void);

void command_flush_all(void);

#endif
