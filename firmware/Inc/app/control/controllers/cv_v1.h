#ifndef CV_V1_H
#define CV_V1_H

#include <stdint.h>

typedef struct {
  float kp;
  float ki;
  uint16_t duty_min;
  uint16_t duty_max;
  uint32_t target_mv;
  uint32_t sample_time_ms;
  uint32_t dt_max_ms;
} cv_config_t;

// Config must remain alive until the next begin for this channel.
void cv_begin(uint32_t channel, const cv_config_t *config);
void cv_service(uint32_t channel);

#endif
