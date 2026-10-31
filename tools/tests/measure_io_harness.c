#include "mock.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "measure_io.h"

dwt_t mock_dwt;
debug_t mock_debug;
uint32_t SystemCoreClock = 480000000U;
I2C_HandleTypeDef hi2c1;
UART_HandleTypeDef huart5;
channel_t channel_a, channel_b, channel_c, channel_d, channel_e;
system_t sys;
static uint32_t tick_us;
static uint16_t registers[2][3];
static uint8_t *pending_buffer;
static uint8_t pending_dev, pending_reg;
static uint16_t pending_size;
static bool pending_write, pending;
static bool fail_data, fail_config, fail_restore, stall_uart;
static unsigned old_transfer, old_completed, stop_count, restart_count, stream_count;
static unsigned sample_packets, final_packets;
static unsigned diagnostic_packets;
static uint8_t last_diagnostic[46];
static uint16_t final_flags;

uint32_t HAL_GetTick(void) { return tick_us / 1000U; }
int HAL_I2C_GetState(I2C_HandleTypeDef *h) { return h->State; }
int HAL_I2C_DeInit(I2C_HandleTypeDef *h) { pending = false; h->State = 0; return HAL_OK; }
int HAL_I2C_Init(I2C_HandleTypeDef *h) { h->State = 0; return HAL_OK; }
void HAL_NVIC_DisableIRQ(int n) { (void)n; }
void HAL_NVIC_EnableIRQ(int n) { (void)n; }
void HAL_NVIC_ClearPendingIRQ(int n) { (void)n; }
channel_t *channel_by_id(uint32_t id) { assert(id == 0U); return &channel_a; }
float ina228_decode(const ina228_t *d, ina228_quantity_t q, const uint8_t *p) {
  (void)d; (void)p; return q == INA228_QTY_CURRENT ? -0.125f : 12.0f;
}
void pwm_stop_all(void) { stop_count++; channel_a.pwm.op_state = PWM_STATE_STOPPED; }
bool pwm_faults_present(void) { return sys.state == SYSTEM_STATE_FAULTED; }
void telem_start_sweeps(void) { restart_count++; }
void stream_init(void) { stream_count++; }

static int start_transfer(uint16_t address, uint16_t reg, uint8_t *p, uint16_t size, bool write) {
  assert(!pending && hi2c1.State == HAL_I2C_STATE_READY);
  assert(address == 0x80U || address == 0x82U);
  pending_dev = (uint8_t)((address - 0x80U) / 2U);
  pending_reg = (uint8_t)reg;
  pending_buffer = p;
  pending_size = size;
  pending_write = write;
  pending = true;
  hi2c1.State = 1;
  return HAL_OK;
}
int HAL_I2C_Mem_Write_IT(I2C_HandleTypeDef *h, uint16_t a, uint16_t r,
                       uint16_t s, uint8_t *p, uint16_t n) {
  (void)h; (void)s; return start_transfer(a, r, p, n, true);
}
int HAL_I2C_Mem_Read_IT(I2C_HandleTypeDef *h, uint16_t a, uint16_t r,
                      uint16_t s, uint8_t *p, uint16_t n) {
  (void)h; (void)s; return start_transfer(a, r, p, n, false);
}
int HAL_UART_Transmit_IT(UART_HandleTypeDef *h, uint8_t *p, uint16_t n) {
  assert(h->gState == 0 && n == 46);
  if (memcmp(p, "MPD1", 4) == 0) {
    diagnostic_packets++;
    memcpy(last_diagnostic,p,46);
    h->gState=1;
    return HAL_OK;
  }
  assert(memcmp(p, "MPM1", 4) == 0);
  uint16_t flags = p[34] | ((uint16_t)p[35] << 8);
  if (flags & MEASURE_VALID) sample_packets++;
  if (flags & MEASURE_END) { final_packets++; final_flags = flags; }
  h->gState = 1;
  return HAL_OK;
}
int HAL_UART_AbortTransmit(UART_HandleTypeDef *h) { h->gState = 0; return HAL_OK; }

#include "../../firmware/Src/drivers/measure_io.c"

static void pump(void) {
  tick_us += 100U;
  mock_dwt.CYCCNT += 48000U;
  if (!stall_uart) huart5.gState = 0;
  if (old_transfer && --old_transfer == 0U) {
    assert(!measure_io_routes_i2c());
    old_completed++;
    hi2c1.State = 0;
  }
  if (pending) {
    bool error = false;
    if (pending_write) {
      uint16_t value = ((uint16_t)pending_buffer[0] << 8) | pending_buffer[1];
      assert(pending_reg == 1U);
      // Even a failed write may have changed the sensor.
      registers[pending_dev][pending_reg] = value;
      if (value == 0xB000U && fail_config) { error = true; fail_config = false; }
      if (value != 0xB000U && fail_restore) error = true;
    } else if (pending_size == 2U) {
      uint16_t value = registers[pending_dev][pending_reg];
      pending_buffer[0] = (uint8_t)(value >> 8);
      pending_buffer[1] = (uint8_t)value;
    } else {
      memset(pending_buffer, 0, pending_size);
      if (fail_data) { error = true; fail_data = false; }
    }
    pending = false;
    hi2c1.State = 0;
    assert(measure_io_routes_i2c());
    if (error) measure_io_i2c_error();
    else measure_io_i2c_complete();
  }
  measure_io_service();
}

static void setup(void) {
  assert(!measure_io_owned());
  tick_us = 0U;
  pending = fail_data = fail_config = fail_restore = stall_uart = false;
  old_transfer = old_completed = stop_count = restart_count = stream_count = 0U;
  sample_packets = final_packets = final_flags = 0U;
  diagnostic_packets=0;
  hi2c1.State = huart5.gState = 0;
  sys.state = SYSTEM_STATE_ACTIVE;
  for (unsigned i = 0; i < 2; i++) {
    registers[i][0] = 0U;
    registers[i][1] = 0xBB6AU;
    registers[i][2] = 3750U;
  }
  assert(measure_io_begin(0));
}

static void finish(void) {
  for (unsigned i = 0; measure_io_owned() && i < 5000; i++) pump();
  assert(!measure_io_owned());
  assert(registers[0][1] == 0xBB6AU && registers[1][1] == 0xBB6AU);
  assert(restart_count == 1 && stream_count == 1);
  assert(!measure_io_routes_i2c());
}

static void until_ready(void) {
  for (unsigned i = 0; !measure_io_ready() && i < 1000; i++) pump();
  assert(measure_io_ready());
}

int main(void) {
  for (unsigned cancel_at = 0; cancel_at < 60; cancel_at++) {
    setup();
    for (unsigned i = 0; i < cancel_at; i++) pump();
    measure_io_end();
    finish();
    assert(final_packets == 1 && !(final_flags & MEASURE_FAULT));
  }
  setup();
  old_transfer = 3;
  hi2c1.State = 1;
  until_ready();
  assert(old_completed == 1);
  for (unsigned i = 0; i < 100; i++) pump();
  assert(sample_packets > 1 && channel_a.telem.valid);
  const mppt_status_t diagnostic = {.target_mv=23900,.error_mv=-80,.power_w=21.025f,
    .age_ms=120,.decisions=12,.window_samples=31,.flags=MPPT_UPDATED|MPPT_POWER_VALID|MPPT_SETTLED,
    .compared_target_mv=24000};
  measure_io_log_mppt(&diagnostic);
  for(unsigned i=0;i<10;i++) pump();
  assert(diagnostic_packets==1 && diagnostic_sequence==1);
  assert(last_diagnostic[12]==(23900 & 255) && last_diagnostic[16]==(uint8_t)-80);
  assert(last_diagnostic[24]==120 && last_diagnostic[28]==12 && last_diagnostic[32]==31);
  assert(last_diagnostic[34]==7 && last_diagnostic[36]==(24000 & 255));
  assert(crc16(last_diagnostic,44)==(uint16_t)(last_diagnostic[44]|(last_diagnostic[45]<<8)));
  // Diagnostic congestion leaves two slots available, without counting raw-data drops.
  const uint32_t raw_drops=dropped;
  for(unsigned i=0;i<QUEUE_SIZE+3;i++) measure_io_log_mppt(&diagnostic);
  assert(count==QUEUE_SIZE-2 && diagnostic_dropped>0 && dropped==raw_drops);
  for(unsigned i=0;i<30;i++) pump();
  measure_io_end();
  const uint32_t diag_sequence_at_stop=diagnostic_sequence;
  measure_io_log_mppt(&diagnostic);
  assert(diagnostic_sequence==diag_sequence_at_stop);
  finish();

  setup();
  until_ready();
  measure_io_set_flags(MEASURE_IV_SWEEP | MEASURE_IV_DUTY_RAMP | MEASURE_IV_RETURN_RAMP |
                       MEASURE_IV_DESCENDING | MEASURE_IV_COMPLETE);
  measure_io_end();
  finish();
  assert(final_flags == (MEASURE_END | MEASURE_IV_SWEEP | MEASURE_IV_DUTY_RAMP | MEASURE_IV_RETURN_RAMP |
                         MEASURE_IV_DESCENDING | MEASURE_IV_COMPLETE));
  setup();
  until_ready();
  measure_io_end();
  finish();
  assert(final_flags == MEASURE_END);

  setup();
  fail_config = true;
  finish();
  assert(stop_count && final_flags & MEASURE_FAULT);

  setup();
  until_ready();
  channel_a.pwm.op_state = PWM_STATE_RUNNING;
  fail_data = true;
  finish();
  assert(stop_count && channel_a.pwm.op_state == PWM_STATE_STOPPED);
  assert(final_flags & MEASURE_FAULT);

  setup();
  until_ready();
  sys.state = SYSTEM_STATE_FAULTED;
  pwm_stop_all();
  measure_io_end();
  finish();
  assert(final_flags & MEASURE_FAULT);

  setup();
  registers[1][2] = 0U;
  finish();
  assert(final_flags & MEASURE_FAULT);

  setup();
  until_ready();
  channel_a.pwm.op_state = PWM_STATE_RUNNING;
  tick_us += 40000U;
  mock_dwt.CYCCNT += 19200000U;
  finish();
  assert(stop_count && final_flags & MEASURE_FAULT);

  setup();
  until_ready();
  stall_uart = true;
  for (unsigned i = 0; i < 500; i++) pump();
  assert(dropped > 0);
  measure_io_end();
  finish();
  assert(measure_io_failed());

  setup();
  until_ready();
  fail_restore = true;
  measure_io_end();
  for (unsigned i = 0; i < 1000; i++) pump();
  assert(measure_io_owned() && measure_io_failed());
  assert(!measure_io_begin(0));
  assert(final_flags & MEASURE_RESTORE_FAILED);
  assert(restart_count == 0);
  puts("lifecycle checks passed");
  return 0;
}
