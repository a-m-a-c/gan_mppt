#include "analog.h"

#include "adc.h"
#include "config.h"
#include "main.h"
#include "system.h"

#define ANALOG_FULL_SCALE 65535U
#define ANALOG_VREF_MV    3300U
#define VBUS_DIV_TOP_OHMS    100000U
#define VBUS_DIV_BOTTOM_OHMS 5230U
#define ANALOG_VBUS_CHANNEL  ADC_CHANNEL_3
#define ANALOG_SAMPLE_TIME   ADC_SAMPLETIME_64CYCLES_5
#define ANALOG_TIMEOUT_MS 2U

static uint32_t analog_last_sweep_ms;

static uint16_t analog_read_raw(void) {
  ADC_ChannelConfTypeDef config = {0};
  uint16_t raw = 0U;

  config.Channel = ANALOG_VBUS_CHANNEL;
  config.Rank = ADC_REGULAR_RANK_1;
  config.SamplingTime = ANALOG_SAMPLE_TIME;
  config.SingleDiff = ADC_SINGLE_ENDED;
  config.OffsetNumber = ADC_OFFSET_NONE;
  config.Offset = 0;
  config.OffsetSignedSaturation = DISABLE;

  if (HAL_ADC_ConfigChannel(&hadc1, &config) != HAL_OK) {
    Error_Handler();
  }

  if (HAL_ADC_Start(&hadc1) == HAL_OK) {
    if (HAL_ADC_PollForConversion(&hadc1, ANALOG_TIMEOUT_MS) == HAL_OK) {
      raw = (uint16_t)HAL_ADC_GetValue(&hadc1);
    }
  }
  (void)HAL_ADC_Stop(&hadc1);

  return raw;
}

static uint32_t raw_to_vbus_mv(uint16_t raw) {
  uint64_t numerator = (uint64_t)raw * ANALOG_VREF_MV *
                       (VBUS_DIV_TOP_OHMS + VBUS_DIV_BOTTOM_OHMS);
  uint64_t denominator = (uint64_t)ANALOG_FULL_SCALE * VBUS_DIV_BOTTOM_OHMS;

  return (uint32_t)(numerator / denominator);
}

void analog_init(void) {
  // Calibration requires ADC disabled; run before the first conversion.
  if (HAL_ADCEx_Calibration_Start(&hadc1, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED) != HAL_OK) {
    Error_Handler();
  }

  analog_last_sweep_ms = HAL_GetTick();
}

void analog_service(void) {
  uint32_t now = HAL_GetTick();

  if ((now - analog_last_sweep_ms) < ANALOG_PERIOD_MS) {
    return;
  }
  analog_last_sweep_ms = now;

  sys.vbus_mv = raw_to_vbus_mv(analog_read_raw());
}
