#include "interrupts.h"
#include "channel_telem.h"
#include "i2c.h"
#include "main.h"
#include "pwm.h"
#include "usart.h"
#include "serial.h"

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
  if (GPIO_Pin == OVP_Pin)
  {
    pwm_OVP_fault();
  }
}

void HAL_HRTIM_Fault1Callback(HRTIM_HandleTypeDef *hhrtim)
{
  UNUSED(hhrtim);
  pwm_OCP_fault(CHANNEL_A);
}

void HAL_HRTIM_Fault2Callback(HRTIM_HandleTypeDef *hhrtim)
{
  UNUSED(hhrtim);
  pwm_OCP_fault(CHANNEL_B);
}

void HAL_HRTIM_Fault3Callback(HRTIM_HandleTypeDef *hhrtim)
{
  UNUSED(hhrtim);
  pwm_OCP_fault(CHANNEL_C);
}

void HAL_HRTIM_Fault4Callback(HRTIM_HandleTypeDef *hhrtim)
{
  UNUSED(hhrtim);
  pwm_OCP_fault(CHANNEL_D);
}

void HAL_HRTIM_Fault5Callback(HRTIM_HandleTypeDef *hhrtim)
{
  UNUSED(hhrtim);
  pwm_OCP_fault(CHANNEL_E);
}

void I2C1_EV_IRQHandler(void)
{
  HAL_I2C_EV_IRQHandler(&hi2c1);
}

void I2C1_ER_IRQHandler(void)
{
  HAL_I2C_ER_IRQHandler(&hi2c1);
}

void HAL_I2C_MemRxCpltCallback(I2C_HandleTypeDef *hi2c)
{
  if (hi2c->Instance == I2C1)
  {
    telem_i2c_complete();
  }
}

void HAL_I2C_ErrorCallback(I2C_HandleTypeDef *hi2c)
{
  if (hi2c->Instance == I2C1)
  {
    telem_i2c_error();
  }
}

void UART5_IRQHandler(void) {
  HAL_UART_IRQHandler(&huart5);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
  if (huart->Instance == UART5) {
    serial_rx_complete();
  }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart) {
  if (huart->Instance == UART5) {
    serial_rx_error();
  }
}
