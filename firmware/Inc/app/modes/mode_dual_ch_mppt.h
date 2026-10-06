#ifndef MODE_DUAL_CH_MPPT_H
#define MODE_DUAL_CH_MPPT_H

#include "mode.h"

mode_request_result_t mode_dual_ch_mppt_begin(void);

mode_state_t mode_dual_ch_mppt_service(bool stopping);

#endif
