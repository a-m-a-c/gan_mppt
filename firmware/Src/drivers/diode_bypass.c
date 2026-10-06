#include "diode_bypass.h"

#include "main.h"

void enable_bypass(bool bypass) {
  if (bypass) {
    HAL_GPIO_WritePin(INJECT_EN_GPIO_Port, INJECT_EN_Pin, GPIO_PIN_SET);
  } else {
    HAL_GPIO_WritePin(INJECT_EN_GPIO_Port, INJECT_EN_Pin, GPIO_PIN_RESET);
  }
}
