#ifndef SERIAL_H
#define SERIAL_H

#include <stdbool.h>
#include <stdint.h>
#include "transport.h"

void serial_init(void);

void serial_service(void);

bool serial_take_next_frame(transport_frame_t *frame);

void serial_rx_complete(void);
void serial_rx_error(void);

bool serial_send(uint8_t id, const uint8_t *data, uint8_t len);

#endif
