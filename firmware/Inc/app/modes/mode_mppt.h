#ifndef MODE_MPPT_H
#define MODE_MPPT_H

#include "mode.h"

mode_request_result_t mode_mppt_begin(void);

mode_state_t mode_mppt_service(bool stopping);

#endif
