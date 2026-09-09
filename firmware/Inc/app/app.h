#ifndef APP_H
#define APP_H
#include <stdbool.h>

extern volatile bool error_flag;

void app_setup(void);

void app_loop(void);

#endif
