// Licensed under the terms in LICENSE, or provided AS-IS if none is supplied.
#ifndef PERTURB_OBSERVE_H
#define PERTURB_OBSERVE_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  float step;
  float target_min;
  float target_max;

  float target;
  float last_power_w;
  int8_t direction;
  bool first_step;
} po_t;

// Requires finite inputs, step > 0 and min <= max.
void po_init(po_t *po, float step, float min, float max, float initial_target);

void po_reset(po_t *po, float initial_target);

// Call only after power has settled following the previous perturbation.
float po_update(po_t *po, float power_w);

#endif
