"""Host-side HAL fault injection; does not verify board behaviour."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

MOCK = r'''
#ifndef MOCK_H
#define MOCK_H
#include <stdint.h>
#include <stdbool.h>
#define CHANNEL_COUNT 5U
#define HAL_OK 0
#define HAL_ERROR 1
#define HAL_I2C_STATE_READY 0
#define HAL_UART_STATE_READY 0
#define I2C_MEMADD_SIZE_8BIT 1
#define I2C1_EV_IRQn 1
#define I2C1_ER_IRQn 2
#define CoreDebug_DEMCR_TRCENA_Msk 1
#define DWT_CTRL_CYCCNTENA_Msk 1
#define SYSTEM_STATE_ACTIVE 1
#define SYSTEM_STATE_FAULTED 2
#define PWM_STATE_STOPPED 0
#define PWM_STATE_RUNNING 1
typedef int HAL_StatusTypeDef;
typedef struct { int State; } I2C_HandleTypeDef;
typedef struct { int gState; } UART_HandleTypeDef;
typedef struct { uint32_t CTRL, CYCCNT; } dwt_t;
typedef struct { uint32_t DEMCR; } debug_t;
extern dwt_t mock_dwt;
extern debug_t mock_debug;
#define DWT (&mock_dwt)
#define CoreDebug (&mock_debug)
extern uint32_t SystemCoreClock;
extern I2C_HandleTypeDef hi2c1;
extern UART_HandleTypeDef huart5;
typedef struct { float vin_v, iin_a, vout_v, iout_a; uint32_t tick_ms; bool valid; } chan_telem_t;
typedef struct { uint16_t duty_applied; int op_state; } pwm_t;
typedef struct { chan_telem_t telem; pwm_t pwm; } channel_t;
extern channel_t channel_a, channel_b, channel_c, channel_d, channel_e;
typedef struct { int state; } system_t;
extern system_t sys;
typedef struct { I2C_HandleTypeDef *i2c; uint8_t address; float shunt_ohms, max_current_a, current_lsb; } ina228_t;
typedef enum { INA228_QTY_BUS_VOLTAGE, INA228_QTY_CURRENT } ina228_quantity_t;
channel_t *channel_by_id(uint32_t id);
float ina228_decode(const ina228_t *, ina228_quantity_t, const uint8_t *);
uint32_t HAL_GetTick(void);
int HAL_I2C_GetState(I2C_HandleTypeDef *);
int HAL_I2C_DeInit(I2C_HandleTypeDef *);
int HAL_I2C_Init(I2C_HandleTypeDef *);
void HAL_NVIC_DisableIRQ(int);
void HAL_NVIC_EnableIRQ(int);
void HAL_NVIC_ClearPendingIRQ(int);
int HAL_I2C_Mem_Write_IT(I2C_HandleTypeDef *, uint16_t, uint16_t, uint16_t, uint8_t *, uint16_t);
int HAL_I2C_Mem_Read_IT(I2C_HandleTypeDef *, uint16_t, uint16_t, uint16_t, uint8_t *, uint16_t);
int HAL_UART_Transmit_IT(UART_HandleTypeDef *, uint8_t *, uint16_t);
int HAL_UART_AbortTransmit(UART_HandleTypeDef *);
void pwm_stop_all(void);
bool pwm_faults_present(void);
void telem_start_sweeps(void);
void stream_init(void);
#endif
'''


class FirmwareLifecycleTests(unittest.TestCase):
    def test_hal_fault_injection(self):
        compiler = shutil.which("gcc")
        if not compiler:
            self.skipTest("Host GCC unavailable")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "mock.h").write_text(MOCK)
            for name in ("channel", "channel_telem", "ina228", "i2c", "usart", "stream", "system", "pwm"):
                (temp / f"{name}.h").write_text('#include "mock.h"\n')
            binary = temp / "measure_io_test.exe"
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(temp), "-I", str(root / "firmware/Inc/drivers"),
                            str(root / "tools/tests/measure_io_harness.c"), "-o", str(binary)],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
            self.assertIn("lifecycle checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
