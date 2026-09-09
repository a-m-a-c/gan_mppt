#ifndef CHECK_H
#define CHECK_H

typedef enum {
  CHECK_RUNNING,
  CHECK_PASSED,
  CHECK_FAILED
} check_result_t;

void check_begin(void);
check_result_t check_service(void);

#endif
