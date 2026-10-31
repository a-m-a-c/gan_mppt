#include <assert.h>
#include <stdio.h>
// Include the implementation to inspect decision state without adding a firmware API.
#include "mppt_v1.c"

channel_t channel_a;
static uint32_t clock_ms;
static uint16_t commanded;
uint32_t HAL_GetTick(void) { return clock_ms; }
channel_t *channel_by_id(uint32_t id) { assert(id == CHANNEL_A); return &channel_a; }
void control_set_duty(uint32_t id, uint16_t duty) { (void)id; commanded = duty; }
static mppt_config_t config = {
  .kp=.01f, .ki=.2f, .duty_max=700, .po_period_ms=100,
  .po_step_mv=100, .po_arrived_mv=75, .po_stall_ms=1000,
  .seed_fraction=1, .target_min_mv=2000, .target_max_mv=30500,
  .dt_max_ms=120, .po_average_ms=30, .po_stable_mv=100
};
static void begin(uint32_t tick) {
  clock_ms=tick;
  channel_a=(channel_t){.telem={.vin_v=24, .iin_a=1, .valid=true, .tick_ms=tick},
                        .pwm={.op_state=PWM_STATE_RUNNING}};
  mppt_begin(CHANNEL_A, &config);
}
static void observe(uint32_t tick, float v, float i) {
  clock_ms=tick;
  channel_a.telem.tick_ms=tick;
  channel_a.telem.vin_v=v;
  channel_a.telem.iin_a=i;
  mppt_observe(CHANNEL_A);
}
int main(void) {
  begin(0);
  float sum=0; unsigned count=0;
  for (uint32_t t=1;t<=120;t++) {
    float v=t%2 ? 24.04f : 23.96f, i=t%2 ? 2 : 1;
    observe(t,v,i);
    if(t>=20) { sum+=v*i; count++; }
    // Duplicate service-loop visits must not overweight a sample.
    mppt_observe(CHANNEL_A);
  }
  assert(mppt_a.window_count==count);
  mppt_service(CHANNEL_A);
  assert(mppt_a.vin_po.target==23900);
  assert(fabsf(mppt_a.vin_po.last_power_w-sum/count)<.0001f);
  assert(mppt_a.window_count==0);
  mppt_status_t status;
  assert(mppt_get_status(CHANNEL_A, &status));
  assert(status.decisions==1 && status.target_mv==23900 && status.compared_target_mv==24000);
  assert((status.flags & (MPPT_UPDATED|MPPT_POWER_VALID|MPPT_SETTLED)) ==
         (MPPT_UPDATED|MPPT_POWER_VALID|MPPT_SETTLED));
  assert(status.age_ms==0 && status.window_samples==count);
  // A new target cannot reuse the completed average.
  clock_ms=240; mppt_service(CHANNEL_A);
  assert(mppt_a.vin_po.target==23900);

  begin(0);
  // A transient arrival, even after the old timeout, is insufficient.
  for(uint32_t t=1;t<=1100;t++) observe(t,23,1);
  observe(1101,24,1); mppt_service(CHANNEL_A);
  assert(mppt_a.vin_po.first_step);
  for(uint32_t t=1102;t<=1131;t++) observe(t,24,1);
  mppt_service(CHANNEL_A);
  assert(!mppt_a.vin_po.first_step);

  begin(0);
  // Every reading is inside +/-75 mV, but a 120 mV span is not settled.
  for(uint32_t t=1;t<=1200;t++) {
    observe(t,t%2 ? 24.06f : 23.94f,1);
    if(t%40==0) mppt_service(CHANNEL_A);
  }
  assert(mppt_a.vin_po.first_step);

  begin(0);
  for(uint32_t t=1;t<=120;t++) observe(t,24,1);
  clock_ms=124; mppt_service(CHANNEL_A);
  assert(mppt_a.vin_po.first_step); // stale completed window
  observe(125,24,1); mppt_service(CHANNEL_A);
  assert(mppt_a.window_count==1 && mppt_a.vin_po.first_step);
  observe(126,24,NAN);
  assert(mppt_a.window_count==0);

  begin(UINT32_MAX-60U);
  for(uint32_t n=1;n<=120;n++) observe(UINT32_MAX-60U+n,24,1);
  mppt_service(CHANNEL_A);
  assert(!mppt_a.vin_po.first_step); // HAL tick wrap

  // A constant +100 mV error gives 0.8 duty units per 40 ms update.
  // With tracking every pass, the fraction is lost; with preservation it accumulates.
  for (unsigned preserve=0;preserve<2;preserve++) {
    config.preserve_pi_fraction=preserve;
    begin(0);
    channel_a.pwm.duty_applied=500;
    mppt_begin(CHANNEL_A,&config);
    for(uint32_t t=40;t<=400;t+=40) {
      observe(t,24.1f,1); mppt_service(CHANNEL_A);
      channel_a.pwm.duty_applied=commanded;
    }
    if(preserve) assert(commanded>=508);
    else assert(commanded==501);
    assert(mppt_get_status(CHANNEL_A,&status) && status.decisions==0 && status.age_ms==400);
  }
  assert(!(status.flags & MPPT_PI_TRACKING));
  // Simulate the actuator being held below the last request by a limiter.
  channel_a.pwm.duty_applied=450;
  observe(440,24.1f,1); mppt_service(CHANNEL_A);
  assert(commanded==450);
  assert(mppt_get_status(CHANNEL_A,&status) && (status.flags & MPPT_PI_TRACKING));
  channel_a.pwm.duty_applied=commanded;
  observe(480,24.1f,1); mppt_service(CHANNEL_A);
  assert(commanded==451); // accumulation resumes immediately after the limiter releases
  begin(0);
  channel_a.pwm.duty_applied=500;
  mppt_begin(CHANNEL_A,&config);
  for(uint32_t t=40;t<=400;t+=40) {
    observe(t,23.9f,1); mppt_service(CHANNEL_A);
    channel_a.pwm.duty_applied=commanded;
  }
  assert(commanded<494); // correction works in both directions
  for(uint32_t t=440;t<=1200;t+=40) {
    observe(t,30,1); mppt_service(CHANNEL_A);
    assert(commanded<=700);
    channel_a.pwm.duty_applied=commanded;
  }
  assert(commanded==700 && mppt_a.vin_pi.clamped);
  observe(1240,23,1); mppt_service(CHANNEL_A);
  assert(commanded<700); // saturation does not trap the integrator
  config.preserve_pi_fraction=false;

  config.po_average_ms=0;
  begin(0);
  observe(1001,23,1); mppt_service(CHANNEL_A);
  assert(!mppt_a.vin_po.first_step); // other modes retain their timeout policy
  puts("MPPT settling checks passed");
}
