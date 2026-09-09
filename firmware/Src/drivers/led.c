#include "led.h"

#include "config.h"
#include "main.h"

void led_set_channel_a(bool on) {
  HAL_GPIO_WritePin(LED_TOG_1_GPIO_Port, LED_TOG_1_Pin, (GPIO_PinState)!on);
}

void led_set_channel_b(bool on) {
  HAL_GPIO_WritePin(LED_TOG_2_GPIO_Port, LED_TOG_2_Pin, (GPIO_PinState)!on);
}

void led_set_channel_c(bool on) {
  HAL_GPIO_WritePin(LED_TOG_3_GPIO_Port, LED_TOG_3_Pin, (GPIO_PinState)!on);
}

void led_set_channel_d(bool on) {
  HAL_GPIO_WritePin(LED_TOG_4_GPIO_Port, LED_TOG_4_Pin, (GPIO_PinState)!on);
}

void led_set_channel_e(bool on) {
  HAL_GPIO_WritePin(LED_TOG_5_GPIO_Port, LED_TOG_5_Pin, (GPIO_PinState)!on);
}

void led_set_active(bool on) {
  HAL_GPIO_WritePin(LED_ACTIVE_GPIO_Port, LED_ACTIVE_Pin, (GPIO_PinState)on);
}

void led_set_err(bool on) {
  HAL_GPIO_WritePin(LED_ERR_GPIO_Port, LED_ERR_Pin, (GPIO_PinState)on);
}

void led_set_out_conn(bool on) {
  HAL_GPIO_WritePin(LED_OUT_CONN_GPIO_Port, LED_OUT_CONN_Pin, (GPIO_PinState)on);
}

void led_init(void) {
  led_set_err(false);
  led_set_out_conn(false);
  led_set_channel_a(false);
  led_set_channel_b(false);
  led_set_channel_c(false);
  led_set_channel_d(false);
  led_set_channel_e(false);
  led_set_active(false);
}

static uint32_t cur_time;
static uint32_t interval;
static bool toggle;
void led_channel_flash_begin(uint32_t interval_ms) {
  cur_time = HAL_GetTick();
  interval = interval_ms;
  toggle = false;
}

void led_channel_flash_service(void) {
  if (HAL_GetTick() - cur_time > interval) {
    led_set_channel_a(toggle);
    led_set_channel_b(toggle);
    led_set_channel_c(toggle);
    led_set_channel_d(toggle);
    led_set_channel_e(toggle);
    toggle = !toggle;
    cur_time = HAL_GetTick();
  }
}
