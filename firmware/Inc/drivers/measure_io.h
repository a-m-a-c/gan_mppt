#ifndef MEASURE_IO_H
#define MEASURE_IO_H

#include <stdbool.h>
#include <stdint.h>

enum {
  MEASURE_VALID = 1U,
  MEASURE_SWITCHING = 2U,
  MEASURE_FAULT = 4U,
  MEASURE_END = 8U,
  MEASURE_RESTORE_FAILED = 16U,
};

typedef struct {
  uint32_t sequence;
  uint32_t time_us;
  uint32_t span_us;
  int32_t vin_mv;
  int32_t iin_ma;
  int32_t vout_mv;
  int32_t iout_ma;
  uint16_t duty;
  uint16_t flags;
  uint32_t dropped;
  uint32_t errors;
} measure_sample_t;

bool measure_io_begin(uint32_t channel);
void measure_io_end(void);
void measure_io_service(void);
bool measure_io_owned(void);
bool measure_io_routes_i2c(void);
bool measure_io_ready(void);
bool measure_io_failed(void);
void measure_io_i2c_complete(void);
void measure_io_i2c_error(void);

#endif
