#ifndef MODE_AUTO_H
#define MODE_AUTO_H

#include "mode.h"

mode_request_result_t mode_auto_begin(void);

mode_state_t mode_auto_service(bool stopping);

#endif
