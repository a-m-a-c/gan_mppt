#ifndef MODE_SINGLE_CH_MPPT_H
#define MODE_SINGLE_CH_MPPT_H

#include <stdint.h>

#include "mode.h"

mode_request_result_t mode_single_ch_mppt_begin(void);

mode_state_t mode_single_ch_mppt_service(bool stopping);

uint16_t mode_single_ch_mppt_target_mv(void);

#endif
