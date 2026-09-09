#ifndef PWM_H
#define PWM_H

#include <stdbool.h>
#include <stdint.h>

#include "channel.h"
#include "config.h"

#define PWM_DUTY_SCALE 1000U

void pwm_init(uint32_t channel);

bool pwm_set_duty_cycle(uint32_t channel, uint16_t duty_cycle);
void pwm_set_dead_time(uint32_t channel, uint16_t dead_time);
void pwm_set_frequency(uint32_t channel, uint32_t frequency);

bool pwm_start(uint32_t channel);
void pwm_stop(uint32_t channel);
void pwm_stop_all(void);

bool pwm_faults_present(void);

bool pwm_clear_faults(void);
bool pwm_clear_OCP_fault(uint32_t channel);
bool pwm_clear_OVP_fault(void);

void pwm_OCP_fault(uint32_t channel);
void pwm_OVP_fault(void);

#endif
