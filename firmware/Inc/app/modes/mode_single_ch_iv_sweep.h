#ifndef MODE_SINGLE_CH_IV_SWEEP_H
#define MODE_SINGLE_CH_IV_SWEEP_H

#include "mode.h"

mode_request_result_t mode_single_ch_iv_sweep_begin(void);

mode_state_t mode_single_ch_iv_sweep_service(bool stopping);

#endif
