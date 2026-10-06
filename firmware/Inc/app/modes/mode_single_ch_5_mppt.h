#ifndef MODE_SINGLE_CH_5_MPPT_H
#define MODE_SINGLE_CH_5_MPPT_H

#include "mode.h"

mode_request_result_t mode_single_ch_5_mppt_begin(void);

mode_state_t mode_single_ch_5_mppt_service(bool stopping);

#endif
