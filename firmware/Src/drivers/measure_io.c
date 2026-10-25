#include "measure_io.h"
#include "channel.h"
#include "channel_telem.h"
#include "ina228.h"
#include "i2c.h"
#include "usart.h"
#include "stream.h"
#include "system.h"
#include "pwm.h"
#include <string.h>

#define SAMPLE_PERIOD_US 1000U
#define TRANSFER_TIMEOUT_MS 10U
#define STALE_TIMEOUT_MS 30U
#define DRAIN_TIMEOUT_MS 250U
#define ADC_FAST 0xB000U
#define QUEUE_SIZE 16U
#define PACKET_SIZE 46U

typedef enum {
  IO_IDLE, IO_HANDOVER, IO_SAVE, IO_CONFIGURE, IO_VERIFY,
  IO_SETTLE, IO_ACQUIRE, IO_RESTORE, IO_RESTORE_VERIFY, IO_DRAIN, IO_HELD
} io_state_t;
typedef enum { XFER_IDLE, XFER_BUSY, XFER_DONE, XFER_ERROR } xfer_state_t;

static io_state_t state;
static volatile xfer_state_t xfer;
static bool routes_i2c;
static bool closing;
static bool failed;
static bool sampled;
static bool final_queued;
static bool tx_active;
static uint32_t channel;
static ina228_t devices[2];
static uint16_t saved_adc[2];
static bool dirty[2];
static uint8_t raw[3];
static uint8_t step;
static uint8_t retries;
static uint32_t phase_ms;
static uint32_t transfer_ms;
static uint32_t sample_start_us;
static uint32_t sample_start_ms;
static uint32_t last_sample_ms;
static uint32_t sequence;
static uint32_t dropped;
static uint32_t errors;
static uint32_t last_cycles;
static uint64_t elapsed_cycles;
static uint32_t cycles_per_us;
static chan_telem_t working;
static measure_sample_t queue[QUEUE_SIZE];
static uint8_t head;
static uint8_t tail;
static uint8_t count;
static uint8_t tx[PACKET_SIZE];

static uint32_t clock_us(void) {
  uint32_t cycles = DWT->CYCCNT;
  elapsed_cycles += (uint32_t)(cycles - last_cycles);
  last_cycles = cycles;
  return (uint32_t)(elapsed_cycles / cycles_per_us);
}

static void put16(uint8_t *p, uint16_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
}

static void put32(uint8_t *p, uint32_t value) {
  for (uint32_t i = 0U; i < 4U; i++) p[i] = (uint8_t)(value >> (8U * i));
}

static uint16_t crc16(const uint8_t *p, uint32_t size) {
  uint16_t crc = 0xFFFFU;
  for (uint32_t i = 0U; i < size; i++) {
    crc ^= (uint16_t)p[i] << 8;
    for (uint32_t bit = 0U; bit < 8U; bit++) {
      crc = (crc & 0x8000U) ? (uint16_t)((crc << 1) ^ 0x1021U) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

static void transmit_service(void) {
  if (huart5.gState != HAL_UART_STATE_READY) return;
  tx_active = false;
  if (count == 0U) return;
  const measure_sample_t *s = &queue[tail];
  memcpy(tx, "MPM1", 4U);
  put32(tx + 4, s->sequence);
  put32(tx + 8, s->time_us);
  put32(tx + 12, s->span_us);
  put32(tx + 16, (uint32_t)s->vin_mv);
  put32(tx + 20, (uint32_t)s->iin_ma);
  put32(tx + 24, (uint32_t)s->vout_mv);
  put32(tx + 28, (uint32_t)s->iout_ma);
  put16(tx + 32, s->duty);
  put16(tx + 34, s->flags);
  put32(tx + 36, s->dropped);
  put32(tx + 40, s->errors);
  put16(tx + 44, crc16(tx, 44U));
  if (HAL_UART_Transmit_IT(&huart5, tx, sizeof(tx)) == HAL_OK) {
    tail = (tail + 1U) % QUEUE_SIZE;
    count--;
    tx_active = true;
  }
}

static void enqueue(bool final, bool restore_failed) {
  channel_t *ch = channel_by_id(channel);
  measure_sample_t sample = {
    .sequence = sequence++,
    .time_us = final ? clock_us() : sample_start_us,
    .span_us = final ? 0U : clock_us() - sample_start_us,
    .vin_mv = final ? 0 : (int32_t)(working.vin_v * 1000.0f),
    .iin_ma = final ? 0 : (int32_t)(working.iin_a * 1000.0f),
    .vout_mv = final ? 0 : (int32_t)(working.vout_v * 1000.0f),
    .iout_ma = final ? 0 : (int32_t)(working.iout_a * 1000.0f),
    .duty = ch->pwm.duty_applied,
    .flags = (uint16_t)(channel << 8),
    .dropped = dropped,
    .errors = errors,
  };
  if (!final) sample.flags |= MEASURE_VALID;
  if (ch->pwm.op_state == PWM_STATE_RUNNING) sample.flags |= MEASURE_SWITCHING;
  if (failed || sys.state == SYSTEM_STATE_FAULTED || pwm_faults_present()) sample.flags |= MEASURE_FAULT;
  if (final) sample.flags |= MEASURE_END;
  if (restore_failed) sample.flags |= MEASURE_RESTORE_FAILED;
  if (count == QUEUE_SIZE) {
    dropped++;
    return;
  }
  queue[head] = sample;
  head = (head + 1U) % QUEUE_SIZE;
  count++;
}

static void fault(void) {
  failed = true;
  closing = true;
  errors++;
  pwm_stop_all();
  channel_by_id(channel)->telem.valid = false;
  sys.state = SYSTEM_STATE_FAULTED;
}

static bool recover_i2c(void) {
  // Outputs are stopped before resetting a stuck transfer; preserve CubeMX timing.
  HAL_NVIC_DisableIRQ(I2C1_EV_IRQn);
  HAL_NVIC_DisableIRQ(I2C1_ER_IRQn);
  HAL_StatusTypeDef stopped = HAL_I2C_DeInit(&hi2c1);
  HAL_StatusTypeDef started = HAL_I2C_Init(&hi2c1);
  HAL_NVIC_ClearPendingIRQ(I2C1_EV_IRQn);
  HAL_NVIC_ClearPendingIRQ(I2C1_ER_IRQn);
  HAL_NVIC_EnableIRQ(I2C1_EV_IRQn);
  HAL_NVIC_EnableIRQ(I2C1_ER_IRQn);
  xfer = XFER_IDLE;
  return stopped == HAL_OK && started == HAL_OK;
}

static void transfer(uint8_t dev, uint8_t reg, bool write, uint16_t value, uint16_t size) {
  if (write) {
    raw[0] = (uint8_t)(value >> 8);
    raw[1] = (uint8_t)value;
  }
  transfer_ms = HAL_GetTick();
  xfer = XFER_BUSY;
  HAL_StatusTypeDef result;
  if (write) {
    result = HAL_I2C_Mem_Write_IT(&hi2c1, devices[dev].address << 1, reg,
                                I2C_MEMADD_SIZE_8BIT, raw, size);
  } else {
    result = HAL_I2C_Mem_Read_IT(&hi2c1, devices[dev].address << 1, reg,
                               I2C_MEMADD_SIZE_8BIT, raw, size);
  }
  if (result != HAL_OK) xfer = XFER_ERROR;
}

static uint16_t reg16(void) {
  return (uint16_t)(((uint16_t)raw[0] << 8) | raw[1]);
}

bool measure_io_begin(uint32_t selected_channel) {
  if (state != IO_IDLE || selected_channel >= CHANNEL_COUNT) return false;
  channel = selected_channel;
  for (uint32_t i = 0U; i < 2U; i++) {
    devices[i] = (ina228_t){.i2c = &hi2c1, .address = (uint8_t)(0x40U + 2U * channel + i),
      .shunt_ohms = 0.003f, .max_current_a = 50.0f, .current_lsb = 50.0f / 524288.0f};
    dirty[i] = false;
  }
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  cycles_per_us = SystemCoreClock / 1000000U;
  if (cycles_per_us == 0U) return false;
  last_cycles = DWT->CYCCNT;
  elapsed_cycles = 0U;
  closing = failed = sampled = final_queued = tx_active = routes_i2c = false;
  sequence = dropped = errors = 0U;
  head = tail = count = step = retries = 0U;
  xfer = XFER_IDLE;
  phase_ms = HAL_GetTick();
  state = IO_HANDOVER;
  return true;
}

void measure_io_end(void) { closing = true; }
bool measure_io_owned(void) { return state != IO_IDLE; }
bool measure_io_routes_i2c(void) { return routes_i2c; }
bool measure_io_ready(void) { return state == IO_ACQUIRE && sampled && !closing; }
bool measure_io_failed(void) { return failed; }
void measure_io_i2c_complete(void) { if (xfer == XFER_BUSY) xfer = XFER_DONE; }
void measure_io_i2c_error(void) { if (xfer == XFER_BUSY) xfer = XFER_ERROR; }

void measure_io_service(void) {
  if (state == IO_IDLE) return;
  const uint32_t now = HAL_GetTick();
  const uint32_t now_us = clock_us();
  transmit_service();
  if (state == IO_HELD) {
    sys.state = SYSTEM_STATE_FAULTED;
    if (!final_queued && count < QUEUE_SIZE) {
      enqueue(true, true);
      final_queued = true;
    }
    return;
  }

  if (state == IO_HANDOVER) {
    if (HAL_I2C_GetState(&hi2c1) != HAL_I2C_STATE_READY) {
      if (now - phase_ms < TRANSFER_TIMEOUT_MS) return;
      fault();
      if (!recover_i2c()) { state = IO_HELD; return; }
    }
    routes_i2c = true;
    state = IO_SAVE;
    step = 0U;
    channel_by_id(channel)->telem.valid = false;
  }

  if (xfer == XFER_BUSY && now - transfer_ms <= TRANSFER_TIMEOUT_MS) return;
  if (xfer == XFER_ERROR || xfer == XFER_BUSY) {
    bool restoring = state == IO_RESTORE || state == IO_RESTORE_VERIFY;
    fault();
    if (!recover_i2c() || (restoring && ++retries >= 3U)) {
      state = IO_HELD;
      return;
    }
    if (!restoring) step = 0U;
    state = IO_RESTORE;
  }

  if (closing && state != IO_RESTORE && state != IO_RESTORE_VERIFY && state != IO_DRAIN) {
    xfer = XFER_IDLE;
    step = 0U;
    state = IO_RESTORE;
  }
  const bool done = xfer == XFER_DONE;
  if (done) xfer = XFER_IDLE;

  switch (state) {
    case IO_SAVE: {
      if (done) {
        uint8_t field = step % 3U;
        uint8_t dev = step / 3U;
        uint16_t cal = (uint16_t)(13107.2e6f * devices[dev].current_lsb * devices[dev].shunt_ohms);
        if ((field == 0U && reg16() != 0U) || (field == 1U && reg16() != cal)) {
          fault();
          return;
        }
        if (field == 2U) saved_adc[dev] = reg16();
        step++;
      }
      if (step == 6U) { step = 0U; state = IO_CONFIGURE; return; }
      static const uint8_t regs[] = {0x00U, 0x02U, 0x01U};
      transfer(step / 3U, regs[step % 3U], false, 0U, 2U);
      break;
    }
    case IO_CONFIGURE:
      if (done) { state = IO_VERIFY; return; }
      dirty[step] = true;
      transfer(step, 0x01U, true, ADC_FAST, 2U);
      break;
    case IO_VERIFY:
      if (done) {
        if (reg16() != ADC_FAST) { fault(); return; }
        if (++step == 2U) { state = IO_SETTLE; phase_ms = now; }
        else state = IO_CONFIGURE;
        return;
      }
      transfer(step, 0x01U, false, 0U, 2U);
      break;
    case IO_SETTLE:
      if (now - phase_ms < 1U) return;
      if (now_us == 0U) { fault(); return; }
      state = IO_ACQUIRE;
      step = 0U;
      sample_start_us = now_us - SAMPLE_PERIOD_US;
      last_sample_ms = now;
      break;
    case IO_ACQUIRE: {
      if (now - last_sample_ms > STALE_TIMEOUT_MS) { fault(); return; }
      if (done) {
        ina228_quantity_t quantity = (step % 2U == 0U) ? INA228_QTY_BUS_VOLTAGE : INA228_QTY_CURRENT;
        float value = ina228_decode(&devices[step / 2U], quantity, raw);
        switch (step) {
          case 0U: working.vin_v = value; break;
          case 1U: working.iin_a = value; break;
          case 2U: working.vout_v = value; break;
          default: working.iout_a = value; break;
        }
        if (++step == 4U) {
          working.tick_ms = sample_start_ms;
          working.valid = true;
          channel_by_id(channel)->telem = working;
          last_sample_ms = now;
          sampled = true;
          enqueue(false, false);
          step = 0U;
          return;
        }
      }
      if (step == 0U) {
        if ((uint32_t)(now_us - sample_start_us) < SAMPLE_PERIOD_US) return;
        sample_start_us = now_us;
        sample_start_ms = now;
      }
      transfer(step / 2U, (step % 2U == 0U) ? 0x05U : 0x07U, false, 0U, 3U);
      break;
    }
    case IO_RESTORE:
      if (done) { state = IO_RESTORE_VERIFY; return; }
      while (step < 2U && !dirty[step]) step++;
      if (step == 2U) { state = IO_DRAIN; phase_ms = now; return; }
      transfer(step, 0x01U, true, saved_adc[step], 2U);
      break;
    case IO_RESTORE_VERIFY:
      if (done) {
        if (reg16() != saved_adc[step]) { xfer = XFER_ERROR; return; }
        dirty[step] = false;
        step++;
        retries = 0U;
        state = IO_RESTORE;
        return;
      }
      transfer(step, 0x01U, false, 0U, 2U);
      break;
    case IO_DRAIN:
      if (!final_queued && count < QUEUE_SIZE) {
        enqueue(true, false);
        final_queued = true;
        return;
      }
      if (now - phase_ms > DRAIN_TIMEOUT_MS) {
        fault();
        (void)HAL_UART_AbortTransmit(&huart5);
        count = 0U;
        tx_active = false;
      }
      if (count != 0U || tx_active || huart5.gState != HAL_UART_STATE_READY) return;
      routes_i2c = false;
      channel_a.telem.valid = channel_b.telem.valid = channel_c.telem.valid = false;
      channel_d.telem.valid = channel_e.telem.valid = false;
      telem_start_sweeps();
      stream_init();
      state = IO_IDLE;
      break;
    default:
      break;
  }
}
