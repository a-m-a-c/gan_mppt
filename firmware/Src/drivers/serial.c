#include "serial.h"
#include "usart.h"
#include <stdint.h>
#include <stdbool.h>
#define UART_IRQ_PRIORITY 4U
#define FRAME_QUEUE_SIZE 16U // Must be a power of two.
#define UART_RX_BUFFER_SIZE 128U // Must be a power of two.

static volatile uint8_t uart_rx_buffer[UART_RX_BUFFER_SIZE];
static volatile uint16_t rx_head = 0;
static volatile uint16_t rx_tail = 0;
static volatile uint16_t rx_dropped = 0;

static void rx_buffer_push(uint8_t byte) {
  const uint16_t next = (rx_head + 1U) & (UART_RX_BUFFER_SIZE - 1U);
  if (next == rx_tail) {
    rx_dropped++;
    return;
  }
  uart_rx_buffer[rx_head] = byte;
  rx_head = next;
}

static bool rx_buffer_pop(uint8_t *byte) {
  if (rx_head == rx_tail) {
    return false;
  }
  *byte = uart_rx_buffer[rx_tail];
  rx_tail = (rx_tail + 1U) & (UART_RX_BUFFER_SIZE - 1U);
  return true;
}

static transport_frame_t frame_queue[FRAME_QUEUE_SIZE];
static uint8_t frame_head = 0;
static uint8_t frame_tail = 0;
static uint16_t frames_dropped = 0;

static bool frame_queue_pop(transport_frame_t *frame) {
  if (frame_head == frame_tail) {
    return false;
  }
  *frame = frame_queue[frame_tail];
  frame_tail = (frame_tail + 1U) & (FRAME_QUEUE_SIZE - 1U);
  return true;
}

static bool frame_queue_push(const transport_frame_t *frame) {
  const uint8_t next = (frame_head + 1U) & (FRAME_QUEUE_SIZE - 1U);
  if (next == frame_tail) {
    return false;
  }
  frame_queue[frame_head] = *frame;
  frame_head = next;
  return true;
}

typedef enum {
  SERIAL_STATE_DECODE_OP,
  SERIAL_STATE_DECODE_SIZE,
  SERIAL_STATE_DECODE_DATA,
  SERIAL_STATE_DECODE_CRC,
  SERIAL_STATE_ERROR
} serial_state_t;

static HAL_StatusTypeDef rx_status;
static serial_state_t serial_state;
static transport_frame_t current_frame;
static uint8_t data_index = 0;

// HAL retains this buffer until the receive interrupt completes.

static uint8_t uart_rx_byte;

void serial_init(void) {
  rx_head = 0;
  rx_tail = 0;
  rx_dropped = 0;
  frame_head = 0;
  frame_tail = 0;
  frames_dropped = 0;
  serial_state = SERIAL_STATE_DECODE_OP;
  current_frame = (transport_frame_t){0};
  data_index = 0;

  HAL_NVIC_SetPriority(UART5_IRQn, UART_IRQ_PRIORITY, 0U);
  HAL_NVIC_EnableIRQ(UART5_IRQn);

  rx_status = HAL_UART_Receive_IT(&huart5, &uart_rx_byte, 1);
}

void serial_service(void) {
  if (serial_state == SERIAL_STATE_ERROR) {
    return;
  }

  uint8_t byte;
  while (rx_buffer_pop(&byte)) {
    switch (serial_state) {
      case SERIAL_STATE_DECODE_OP:
        current_frame = (transport_frame_t){0};
        data_index = 0;

        current_frame.op = byte;
        serial_state = SERIAL_STATE_DECODE_SIZE;
        break;
      case SERIAL_STATE_DECODE_SIZE:
        if (byte > TRANSPORT_MAX_PAYLOAD) {
          serial_state = SERIAL_STATE_ERROR;
          break;
        }
        current_frame.len = byte;
        if (current_frame.len == 0) {
          serial_state = SERIAL_STATE_DECODE_CRC;
        } else {
          serial_state = SERIAL_STATE_DECODE_DATA;
        }
        break;
      case SERIAL_STATE_DECODE_DATA:
        current_frame.data[data_index] = byte;
        data_index++;
        if (data_index >= current_frame.len) {
          serial_state = SERIAL_STATE_DECODE_CRC;
        }
        break;
      case SERIAL_STATE_DECODE_CRC:
        // CRC validation is not implemented.
        if (!frame_queue_push(&current_frame)) {
          frames_dropped++;
        }
        serial_state = SERIAL_STATE_DECODE_OP;
        break;
      case SERIAL_STATE_ERROR:
        return;
    }
  }
}

bool serial_take_next_frame(transport_frame_t *frame) {
  return frame_queue_pop(frame);
}

void serial_rx_complete(void) {
  rx_buffer_push(uart_rx_byte);
  rx_status = HAL_UART_Receive_IT(&huart5, &uart_rx_byte, 1);
}

void serial_rx_error(void) {
  rx_status = HAL_UART_Receive_IT(&huart5, &uart_rx_byte, 1);
}

#define TX_BUFFER_SIZE (2U + TRANSPORT_MAX_PAYLOAD)

static uint8_t tx_buffer[TX_BUFFER_SIZE];
static HAL_StatusTypeDef tx_status;
static uint16_t tx_dropped = 0;

bool serial_send(uint8_t id, const uint8_t *data, uint8_t len) {
  if ((len > TRANSPORT_MAX_PAYLOAD) || ((data == NULL) && (len > 0U))) {
    return false;
  }

  // The transmit ISR still owns tx_buffer until UART is ready.
  if (huart5.gState != HAL_UART_STATE_READY) {
    tx_dropped++;
    return false;
  }

  tx_buffer[0] = id;
  tx_buffer[1] = len;
  for (uint8_t i = 0; i < len; i++) {
    tx_buffer[2U + i] = data[i];
  }

  tx_status = HAL_UART_Transmit_IT(&huart5, tx_buffer, (uint16_t)(2U + len));
  return tx_status == HAL_OK;
}
