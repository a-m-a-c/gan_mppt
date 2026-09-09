#include "status.h"
#include "led.h"
#include "channel.h"
#include "system.h"
#include "config.h"
#include "main.h"
#include <stdint.h>
#define BLINK_DELAY_MS 200
static uint32_t last_blink_time_ms = 0;
static bool channel_led_state[5] = {false, false, false, false, false};
static uint8_t cur_led = 0;
void status_service(void) {
  system_state_t state = sys.state;
  led_set_err(state == SYSTEM_STATE_FAULTED);
  led_set_active(state == SYSTEM_STATE_ACTIVE);

  if (sys.vbus_mv > LED_BUS_ON_MV) {
    led_set_out_conn(true);
  } else if (sys.vbus_mv < LED_BUS_OFF_MV) {
    led_set_out_conn(false);
  }

  if (state != SYSTEM_STATE_ACTIVE) {
    uint32_t current_time_ms = HAL_GetTick();
    if (current_time_ms - last_blink_time_ms >= BLINK_DELAY_MS) {
      last_blink_time_ms = current_time_ms;

      if (cur_led < 5) {
        channel_led_state[cur_led] = true;
        cur_led++;
      } else {
        for (int i = 0; i < 5; i++) {
          channel_led_state[i] = false;
        }
        cur_led = 0;
      }
      led_set_channel_a(channel_led_state[0]);
      led_set_channel_b(channel_led_state[1]);
      led_set_channel_c(channel_led_state[2]);
      led_set_channel_d(channel_led_state[3]);
      led_set_channel_e(channel_led_state[4]);
    }
  } else {
    led_set_channel_a(channel_a.pwm.op_state == PWM_STATE_RUNNING);
    led_set_channel_b(channel_b.pwm.op_state == PWM_STATE_RUNNING);
    led_set_channel_c(channel_c.pwm.op_state == PWM_STATE_RUNNING);
    led_set_channel_d(channel_d.pwm.op_state == PWM_STATE_RUNNING);
    led_set_channel_e(channel_e.pwm.op_state == PWM_STATE_RUNNING);
  }
}
