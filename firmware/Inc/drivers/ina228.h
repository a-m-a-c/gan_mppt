#ifndef INA228_H
#define INA228_H

#include <stdbool.h>
#include <stdint.h>
#include "stm32h7xx_hal.h"

typedef struct
{
  I2C_HandleTypeDef *i2c;
  uint8_t address;     // 7-bit I2C address.
  float shunt_ohms;
  float max_current_a;
  float current_lsb;
} ina228_t;

typedef enum
{
  INA228_QTY_BUS_VOLTAGE = 0,
  INA228_QTY_CURRENT
} ina228_quantity_t;

uint8_t ina228_register(ina228_quantity_t quantity);
uint16_t ina228_register_size(ina228_quantity_t quantity);
float ina228_decode(const ina228_t *dev, ina228_quantity_t quantity, const uint8_t *raw);

bool ina228_init(ina228_t *dev);
#endif
