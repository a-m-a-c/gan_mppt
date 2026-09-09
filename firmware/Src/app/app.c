#include <stdbool.h>
#include <stdint.h>

#include "app.h"

#include "channel.h"
#include "system.h"
#include "led.h"
#include "pwm.h"
#include "config.h"
#include "main.h"
#include "analog.h"
#include "check.h"
#include "status.h"
#include "channel_telem.h"
#include "mode.h"
#include "command.h"
#include "stream.h"
#include "serial.h"

volatile bool error_flag = false;

void app_setup(void) {
  channel_init_all();
  system_init();
  led_init();
  analog_init();
  telem_init(CHANNEL_A);
  telem_init(CHANNEL_B);
  telem_init(CHANNEL_C);
  telem_init(CHANNEL_D);
  telem_init(CHANNEL_E);
  telem_start_sweeps();
  pwm_init(CHANNEL_A);
  pwm_init(CHANNEL_B);
  pwm_init(CHANNEL_C);
  pwm_init(CHANNEL_D);
  pwm_init(CHANNEL_E);
  serial_init();
  command_init();
  stream_init();
}

static system_state_t prev_state = SYSTEM_STATE_INIT;

static void app_fault_service(void) {
  if (sys.state == SYSTEM_STATE_RESET) return;

  if (pwm_faults_present()) {
    sys.state = SYSTEM_STATE_FAULTED;
  }
  if (sys.state == SYSTEM_STATE_FAULTED && prev_state != SYSTEM_STATE_FAULTED) {
    pwm_stop_all();
    sys.mode = MODE_NONE;
  }
}

void app_loop(void) {
  serial_service();
  command_service();
  if (system_command_received(SYSTEM_COMMAND_RESET)) sys.state = SYSTEM_STATE_RESET;
  app_fault_service();

  const bool entered = (sys.state != prev_state);
  prev_state = sys.state;

  switch (sys.state) {
    case SYSTEM_STATE_INIT: {
      sys.state = SYSTEM_STATE_CHECK;
      break;
    }

    case SYSTEM_STATE_CHECK: {
      if (entered) check_begin();

      check_result_t result = check_service();

      switch (result) {
        case CHECK_RUNNING:
          break;
        case CHECK_FAILED:
          sys.state = SYSTEM_STATE_FAULTED;
          break;
        case CHECK_PASSED:
          sys.state = SYSTEM_STATE_STANDBY;
          break;
      }
      break;
    }

    case SYSTEM_STATE_STANDBY: {
      if (entered) pwm_stop_all();

      sys.mode = system_command_requested_mode();

      if (sys.mode != MODE_NONE) sys.state = SYSTEM_STATE_ACTIVE;
      break;
    }

    case SYSTEM_STATE_ACTIVE: {
      if (entered) {
        mode_request_result_t init_result = mode_begin(sys.mode);
        switch (init_result) {
          case MODE_INIT_FAULT:
            sys.state = SYSTEM_STATE_FAULTED;
            break;
          case MODE_INIT_REFUSED:
            sys.mode = MODE_NONE;
            sys.state = SYSTEM_STATE_STANDBY;
            break;
          case MODE_INIT_OK:
            break;
        }
      }
      if (sys.state != SYSTEM_STATE_ACTIVE) break;

      const bool stop_request = system_command_received(SYSTEM_COMMAND_STOP);
      mode_state_t state = mode_service(stop_request);

      switch (state) {
        case MODE_STATE_RUNNING:
          break;
        case MODE_STATE_FAULTED:
          sys.state = SYSTEM_STATE_FAULTED;
          break;
        case MODE_STATE_EXIT:
          sys.mode = MODE_NONE;
          sys.state = SYSTEM_STATE_STANDBY;
          break;
      }
      break;
    }

    case SYSTEM_STATE_FAULTED: {
      if (system_command_received(SYSTEM_COMMAND_CLEAR_FAULT) && pwm_clear_faults()) {
        sys.state = SYSTEM_STATE_CHECK;
      }
      break;
    }

    case SYSTEM_STATE_RESET: {
      if (entered) {
        pwm_stop_all();
        HAL_NVIC_SystemReset();
      }
      error_flag = true;
      break;
    }
  }

  app_fault_service();

  analog_service();
  status_service();
  stream_service();
  telem_service();
  command_flush_all();
}
