#ifndef CHANNEL_TELEM_H
#define CHANNEL_TELEM_H

#include <stdbool.h>
#include <stdint.h>

bool telem_init(uint32_t channel);

// Call after all telem_init() calls; blocking and interrupt transfers must not overlap.
void telem_start_sweeps(void);

void telem_service(void);

uint32_t telem_sweep_age_ms(void);

uint32_t telem_error_count(uint32_t channel);

void telem_i2c_complete(void);
void telem_i2c_error(void);

#endif
