// Licensed under the terms in LICENSE, or provided AS-IS if none is supplied.
#include "perturb_observe.h"

static float clamp_target(const po_t *po, float target) {
  if (target < po->target_min) return po->target_min;
  if (target > po->target_max) return po->target_max;
  return target;
}

void po_init(po_t *po, float step, float min, float max, float initial_target) {
  po->step = step;
  po->target_min = min;
  po->target_max = max;
  po_reset(po, initial_target);
}

void po_reset(po_t *po, float initial_target) {
  po->target = clamp_target(po, initial_target);
  po->last_power_w = 0.0f;
  po->direction = -1;
  po->first_step = true;
}

float po_update(po_t *po, float power_w) {
  if (!po->first_step && power_w < po->last_power_w) {
    po->direction = (int8_t)-po->direction;
  }
  po->first_step = false;
  po->last_power_w = power_w;

  const float stepped = po->target + po->direction * po->step;
  po->target = clamp_target(po, stepped);

  if (po->target != stepped) {
    po->direction = (int8_t)-po->direction;
  }
  return po->target;
}
