#ifndef TRANSPORT_H
#define TRANSPORT_H

#include <stdint.h>

// Classic CAN limits the shared payload to 8 bytes.

#define TRANSPORT_MAX_PAYLOAD 8U

typedef struct {
  uint8_t op;
  uint8_t len;
  uint8_t data[TRANSPORT_MAX_PAYLOAD];
} transport_frame_t;

#endif
