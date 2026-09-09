#ifndef MODE_H
#define MODE_H
#include <stdbool.h>

typedef enum {
  MODE_NONE,
  MODE_SINGLE_CH_MPPT,
  MODE_SINGLE_CH_CV,
  MODE_SINGLE_CH_IV_SWEEP,
  MODE_MPPT
} mode_t;

typedef enum {
  MODE_STATE_RUNNING,
  MODE_STATE_FAULTED,
  MODE_STATE_EXIT
} mode_state_t;

typedef enum {
    MODE_INIT_REFUSED,
    MODE_INIT_FAULT,
    MODE_INIT_OK
} mode_request_result_t;

mode_request_result_t mode_begin(mode_t mode);
mode_state_t mode_service(bool stop_request);

#endif
