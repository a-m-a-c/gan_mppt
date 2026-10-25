#ifndef MODE_MEASURE_H
#define MODE_MEASURE_H

#include "mode.h"

mode_request_result_t mode_measure_begin(void);
mode_state_t mode_measure_service(bool stopping);
void mode_measure_end(void);

#endif
