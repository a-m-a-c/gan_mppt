#ifndef MODE_PI_TEST_H
#define MODE_PI_TEST_H
#include "mode.h"
#include <stdint.h>

mode_request_result_t mode_pi_test_begin(void);
mode_state_t mode_pi_test_service(bool stopping);
void mode_pi_test_end(void);
bool mode_pi_test_set_target(uint16_t target_mv);
#endif
