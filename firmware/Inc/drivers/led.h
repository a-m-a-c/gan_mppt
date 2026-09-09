// This software is licensed under terms that can be found in the LICENSE file
// in the root directory of this software component.
// If no LICENSE file comes with this software, it is provided AS-IS.
#ifndef LED_H
#define LED_H

#include <stdbool.h>
#include <stdint.h>

void led_init(void);

void led_set_channel_a(bool on);
void led_set_channel_b(bool on);
void led_set_channel_c(bool on);
void led_set_channel_d(bool on);
void led_set_channel_e(bool on);

void led_set_active(bool on);
void led_set_err(bool on);
void led_set_out_conn(bool on);

void led_channel_flash_begin(uint32_t interval_ms);
void led_channel_flash_service(void);

#endif
