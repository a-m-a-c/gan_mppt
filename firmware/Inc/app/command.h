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
    SYSTEM_COMMAND_RUN_MPPT,
    SYSTEM_COMMAND_RUN_SINGLE_CH_CV,
    SYSTEM_COMMAND_RUN_SINGLE_CH_MPPT,
    SYSTEM_COMMAND_RUN_SINGLE_CH_IV_SWEEP
} system_commands_t;

void command_init(void);

mode_t system_command_requested_mode(void);

bool system_command_received(system_commands_t command);

void command_service(void);

void command_flush_all(void);

#endif
