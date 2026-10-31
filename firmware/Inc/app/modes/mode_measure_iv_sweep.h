#ifndef MODE_MEASURE_IV_SWEEP_H
#define MODE_MEASURE_IV_SWEEP_H

#include "mode.h"

mode_request_result_t mode_measure_iv_sweep_begin(void);
mode_state_t mode_measure_iv_sweep_service(bool stopping);
void mode_measure_iv_sweep_end(void);

#endif
