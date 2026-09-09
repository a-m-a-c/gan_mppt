/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32h7xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define I_IND_2_Pin GPIO_PIN_3
#define I_IND_2_GPIO_Port GPIOF
#define I_IND_3_Pin GPIO_PIN_5
#define I_IND_3_GPIO_Port GPIOF
#define I_IND_4_Pin GPIO_PIN_7
#define I_IND_4_GPIO_Port GPIOF
#define I_IND_5_Pin GPIO_PIN_9
#define I_IND_5_GPIO_Port GPIOF
#define LED_TOG_1_Pin GPIO_PIN_4
#define LED_TOG_1_GPIO_Port GPIOA
#define LED_TOG_2_Pin GPIO_PIN_5
#define LED_TOG_2_GPIO_Port GPIOA
#define V_BUS_DIV_Pin GPIO_PIN_6
#define V_BUS_DIV_GPIO_Port GPIOA
#define NTC_CH5_Pin GPIO_PIN_7
#define NTC_CH5_GPIO_Port GPIOA
#define NTC_CH4_Pin GPIO_PIN_4
#define NTC_CH4_GPIO_Port GPIOC
#define LED_TOG_3_Pin GPIO_PIN_5
#define LED_TOG_3_GPIO_Port GPIOC
#define LED_TOG_4_Pin GPIO_PIN_0
#define LED_TOG_4_GPIO_Port GPIOB
#define NTC_CH3_Pin GPIO_PIN_1
#define NTC_CH3_GPIO_Port GPIOB
#define LED_TOG_5_Pin GPIO_PIN_2
#define LED_TOG_5_GPIO_Port GPIOB
#define NTC_CH2_Pin GPIO_PIN_11
#define NTC_CH2_GPIO_Port GPIOF
#define NTC_CH1_Pin GPIO_PIN_12
#define NTC_CH1_GPIO_Port GPIOF
#define I_IND_1_Pin GPIO_PIN_13
#define I_IND_1_GPIO_Port GPIOF
#define LED_ACTIVE_Pin GPIO_PIN_9
#define LED_ACTIVE_GPIO_Port GPIOD
#define LED_ERR_Pin GPIO_PIN_10
#define LED_ERR_GPIO_Port GPIOD
#define LED_OUT_CONN_Pin GPIO_PIN_11
#define LED_OUT_CONN_GPIO_Port GPIOD
#define OVP_Pin GPIO_PIN_10
#define OVP_GPIO_Port GPIOC
#define OVP_EXTI_IRQn EXTI15_10_IRQn
#define INJECT_EN_Pin GPIO_PIN_5
#define INJECT_EN_GPIO_Port GPIOD

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
