#include "stream.h"
#include "main.h"
#include "system.h"
#include "channel.h"
#include "serial.h"
#include <stdint.h>
#include <stdbool.h>
#include <math.h>

// 146 bytes * 10 bits / 921600 baud = 1.59 ms per set; 5 ms leaves headroom.
#define STREAM_PERIOD_MS 5U
#define STREAM_PACKET_COUNT 26U

// [id:u8][size:u8][data:little endian]. Columns: vin, iin, vout, iout, duty.
static const uint8_t channel_ids[CHANNEL_COUNT][5] = {
  {0x63U, 0x64U, 0x66U, 0x67U, 0x61U},
  {0x70U, 0x71U, 0x72U, 0x73U, 0x74U},
  {0x80U, 0x81U, 0x82U, 0x83U, 0x84U},
  {0x90U, 0x91U, 0x92U, 0x93U, 0x94U},
  {0xA0U, 0xA1U, 0xA2U, 0xA3U, 0xA4U},
};

static uint32_t last_send_ms;
static uint8_t next_packet;
static uint32_t vbus_mv;
static chan_telem_t telem[CHANNEL_COUNT];
static uint16_t duty[CHANNEL_COUNT];

static void put_u32(uint8_t *out, uint32_t value) {
  out[0] = (uint8_t)value;
  out[1] = (uint8_t)(value >> 8);
  out[2] = (uint8_t)(value >> 16);
  out[3] = (uint8_t)(value >> 24);
}

// Invalid readings use reserved values instead of a separate flags packet.
static uint32_t volts_to_mv(float v, bool valid) {
  if (!valid || !isfinite(v)) return UINT32_MAX;
  if (v <= 0.0f) return 0U;
  return (uint32_t)(v * 1000.0f);
}

static uint32_t amps_to_ma(float a, bool valid) {
  if (!valid || !isfinite(a)) return (uint32_t)INT32_MIN;
  return (uint32_t)(int32_t)(a * 1000.0f);
}

static bool send_packet(uint8_t index) {
  uint8_t payload[4];
  if (index == 0U) {
    put_u32(payload, vbus_mv);
    return serial_send(0x60U, payload, 4U);
  }

  const uint8_t ch = (index - 1U) / 5U;
  const uint8_t field = (index - 1U) % 5U;
  const chan_telem_t *sample = &telem[ch];
  switch (field) {
    case 0:
      put_u32(payload, volts_to_mv(sample->vin_v, sample->valid));
      break;
    case 1:
      put_u32(payload, amps_to_ma(sample->iin_a, sample->valid));
      break;
    case 2:
      put_u32(payload, volts_to_mv(sample->vout_v, sample->valid));
      break;
    case 3:
      put_u32(payload, amps_to_ma(sample->iout_a, sample->valid));
      break;
    case 4:
      payload[0] = (uint8_t)duty[ch];
      payload[1] = (uint8_t)(duty[ch] >> 8);
      return serial_send(channel_ids[ch][field], payload, 2U);
    default:
      return false;
  }
  return serial_send(channel_ids[ch][field], payload, 4U);
}

void stream_init(void) {
  last_send_ms = HAL_GetTick();
  next_packet = STREAM_PACKET_COUNT;
}

void stream_service(void) {
  const uint32_t now = HAL_GetTick();
  if (next_packet >= STREAM_PACKET_COUNT) {
    if ((now - last_send_ms) < STREAM_PERIOD_MS) return;
    last_send_ms = now;
    next_packet = 0;
    vbus_mv = sys.vbus_mv;
    telem[CHANNEL_A] = channel_a.telem;
    telem[CHANNEL_B] = channel_b.telem;
    telem[CHANNEL_C] = channel_c.telem;
    telem[CHANNEL_D] = channel_d.telem;
    telem[CHANNEL_E] = channel_e.telem;
    duty[CHANNEL_A] = channel_a.pwm.duty_applied;
    duty[CHANNEL_B] = channel_b.pwm.duty_applied;
    duty[CHANNEL_C] = channel_c.pwm.duty_applied;
    duty[CHANNEL_D] = channel_d.pwm.duty_applied;
    duty[CHANNEL_E] = channel_e.pwm.duty_applied;
  }

  if (send_packet(next_packet)) next_packet++;
}
