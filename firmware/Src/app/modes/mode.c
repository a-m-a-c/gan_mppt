#include "mode.h"
#include <stdbool.h>

#include "mode_mppt.h"
#include "mode_single_ch_cv.h"
#include "mode_single_ch_iv_sweep.h"
#include "mode_single_ch_mppt.h"

static mode_t active_mode = MODE_NONE;
static bool stopping = false;

mode_request_result_t mode_begin(mode_t mode) {
  active_mode = mode;
  stopping = false;

  switch (mode) {
    case MODE_MPPT:
      return mode_mppt_begin();
    case MODE_SINGLE_CH_CV:
      return mode_single_ch_cv_begin();
    case MODE_SINGLE_CH_IV_SWEEP:
      return mode_single_ch_iv_sweep_begin();
    case MODE_SINGLE_CH_MPPT:
      return mode_single_ch_mppt_begin();
    case MODE_NONE:
      break;
  }

  return MODE_INIT_REFUSED;
}

mode_state_t mode_service(bool stop_request) {
  if (stop_request) stopping = true;

  switch (active_mode) {
    case MODE_MPPT:
      return mode_mppt_service(stopping);
    case MODE_SINGLE_CH_CV:
      return mode_single_ch_cv_service(stopping);
    case MODE_SINGLE_CH_IV_SWEEP:
      return mode_single_ch_iv_sweep_service(stopping);
    case MODE_SINGLE_CH_MPPT:
      return mode_single_ch_mppt_service(stopping);
    case MODE_NONE:
      break;
  }

  return MODE_STATE_EXIT;
}
