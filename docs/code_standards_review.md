# Coding standards review — NASA/JPL Power of 10 and general firmware conventions

Audit date 2026-08-24, commit `99f8748`. Scope is hand-written code only:
`Src/app/`, `Src/drivers/`, `Inc/app/`, `Inc/drivers/`, `Inc/config.h` and the
build files. CubeMX-generated code and the HAL are excluded — they are not ours
to change and would fail most of these rules anyway. `Src/main.c` is in scope
**only inside the USER CODE markers**, which is where `Error_Handler()` lives.

## The standard this is measured against

This firmware controls five synchronous boost converters handling over 1 kW
total and charges a 32.5–54.6 V battery pack. The credible failure modes are
converter destruction and battery overcharge, both energetic. Power of 10 was
written for exactly this class of system, and it applies here in full — a rule
is not worth skipping because the code is young or the interface is a bench
console.

Two hardware facts from [hardware.md](.agents/hardware.md) set the weighting
for everything below, and both push in the same direction:

- **Overvoltage protection is software.** The 55 V comparator is wired to
  PC10/EXTI15_10, not to an HRTIM fault input — all five fault inputs are
  consumed by the per-channel OCP and there is no sixth. Overcurrent is killed
  in silicon before software sees it; **overvoltage is killed only if the EXTI
  handler runs.** Every "the CPU could hang" or "interrupts could be masked"
  finding below is therefore a battery-overvoltage finding, not a robustness
  nicety.
- **The load-bearing duty protection is not implemented yet.** The dynamic
  ceiling `D_max = 1 - Vin/Vbus + margin` is a TODO, and `hardware.md` states
  plainly that the static 85 % ceiling "is a crude backstop, not real
  protection". The measured 2026-08-23 result is that a duty *step* trips OCP
  long before the ceiling matters, so the slew limit is doing the real work —
  and it currently lives inside one mode file (see H3).

This is a sample of findings, not an exhaustive list.

---

## Part 1 — Hazard-ordered findings

These are ordered by what they can do to the hardware, not by which rule they
break. All five are also Power of 10 violations and are cross-referenced.

### H1 — A single ADC failure commands maximum duty (Rule 7)

`analog.c` returns 0 from `analog_read_raw()` on any failure, and both the code
comment ([analog.c:19](Src/drivers/analog.c#L19)) and `hardware.md` justify
this the same way:

> "Zero on any failure, which reads as 0 V — out of range for every consumer,
> so a dead ADC degrades to 'no reading' rather than a plausible wrong one."

**No consumer implements that check.** The only closed loop in the firmware,
[mode_single_ch_cv.c:78](Src/app/modes/mode_single_ch_cv.c#L78), feeds
`sys.vbus_mv` straight into the PI controller:

```c
uint16_t duty = (uint16_t)pi_update(&volt_pi, (float)VOUT_MV, (float)sys.vbus_mv, (float)dt_ms);
```

A stuck-at-zero reading is not "no reading" to a voltage loop — it is the
**largest possible error**, and the controller responds by driving duty up. With
`KI = 0.6` at a 1 ms step the integrator alone adds 15 units per pass, so the PI
output saturates at `MAX_DUTY_CYCLE` (750) in about 40 ms, and the slew limiter
walks the applied duty there in about 50 ms. At 75 % duty a boost stage runs at
4× gain.

Nothing catches it on the way:

- The setpoint is 25 V and OVP trips at 55 V, so a runaway to ~48 V from a 12 V
  bench input sits in the gap between them, unnoticed by both.
- There is **no plausibility check anywhere** — nothing compares the measurement
  against the setpoint and notices the loop is 23 V from target and still
  pushing.
- The mode's fault check ([mode_single_ch_cv.c:50](Src/app/modes/mode_single_ch_cv.c#L50))
  tests `sys.ovp_latched` and `ocp_latched` only. It never re-reads
  `channel_a.telem.valid`, which is checked once in `_begin()` and never again,
  even though the I2C recovery path can invalidate a channel mid-run
  ([channel_telem.c:261](Src/drivers/channel_telem.c#L261)).
- `chan_telem_t.tick_ms` exists for staleness detection and
  `telem_sweep_age_ms()` exists to report it. **Neither is read anywhere.**

Per the project's own rule — "if a number here disagrees with the code, one of
them is a bug, say so rather than silently trusting either" — the doc and the
code disagree, and the code is the bug. `analog.c`'s degradation strategy is
sound but only half-built: it needs a validity flag alongside `sys.vbus_mv`
that every loop is required to check before closing on it.

CV is a bench mode and the header comment already warns against running it with
a battery on the output, so today's exposure is converter-side. The reason this
is H1 rather than a bench curiosity is that **the pattern — closing a control
loop on an unvalidated measurement — is what the MPPT and charging modes will
inherit**, and those do face the battery.

Related: `hardware.md` records that the ADC kernel clock is 76 MHz against a
50 MHz ceiling, out of spec and a pending CubeMX fix. The measurement gating
battery voltage comes from that ADC.

### H2 — `Error_Handler()` disables OVP and leaves the stage switching (Rule 2)

[main.c:250](Src/main.c#L250):

```c
void Error_Handler(void) {
  /* USER CODE BEGIN Error_Handler_Debug */
  __disable_irq();
  while (1) {}
  /* USER CODE END Error_Handler_Debug */
}
```

Because OVP is an EXTI interrupt, `__disable_irq()` **permanently disables the
board's only overvoltage protection**, and the infinite loop guarantees it never
comes back. The HRTIM is untouched, so every running channel keeps switching at
its last commanded duty, forever, with no overvoltage limit. Per-channel OCP
still works — that one is in silicon — so the stage is protected against
overcurrent and unprotected against overcharge, which is the wrong way round for
a battery.

This is reachable from ordinary operation, not just from a bug:
`pwm_init()`, `pwm_set_frequency()`, `pwm_set_dead_time()`
([pwm.c:230, 300, 327, 341](Src/drivers/pwm.c#L230)) and
`analog_read_raw()` ([analog.c:34](Src/drivers/analog.c#L34)) all call it on a
HAL non-OK return.

Worse, [pwm_start()](Src/drivers/pwm.c#L350) calls `pwm_set_duty_cycle()`
*inside* `enter_critical()`, and that path reaches `HAL_HRTIM_SoftwareUpdate()`
and so `Error_Handler()` — entering the trap with interrupts already disabled,
while a channel is being brought up.

The whole body sits inside the USER CODE markers, so it is ours to fix within
the CubeMX rules. The minimum is to stop the outputs before trapping:

```c
void Error_Handler(void) {
  /* USER CODE BEGIN Error_Handler_Debug */
  all_channels_stop_hw();   /* register writes only - no HAL, no locks */
  __disable_irq();
  while (1) {}
  /* USER CODE END Error_Handler_Debug */
}
```

`pwm.c` already has the lock-free register-write stop for exactly this reason.
It needs exposing, or duplicating, as something callable from a context where
nothing can be assumed.

### H3 — The load-bearing safety limit lives inside one mode

`README.md` states the rule:

> "The gate is unconditional. Slider, MPPT, CV loop, CAN — every duty is
> slew-limited and ceiling-clamped in one place, with no path around it."

`Src/app/control/control.c` is a **one-line stub**. The slew limiter is
implemented at [mode_single_ch_cv.c:83-95](Src/app/modes/mode_single_ch_cv.c#L83),
and `DUTY_SLEW_PER_STEP` is a `#define` local to that file.

`hardware.md` establishes by measurement that this constant is the primary
protection: 150 units/step clean, 218 units/step trips OCP, "the slew limit, not
the ceiling, is the load-bearing half of the duty gate." So the mechanism the
board most depends on is currently opt-in per mode. `mode_mppt.c` and
`mode_single_ch_mppt.c` are stubs today; when either is written, it gets no slew
limiting unless its author remembers to add it, and nothing in the build or the
type system will say so.

This is an architecture gap rather than a Power of 10 violation, but it is the
one with the largest blast radius, and the architecture doc already specifies
the fix. `hardware.md` also notes the bench-derived constant is roughly **6× too
permissive** on the array, so the gate needs to scale with operating point
rather than be a copied constant regardless.

Secondary, and it compounds this: [mode_single_ch_cv.c:23](Src/app/modes/mode_single_ch_cv.c#L23)
defines a local `MAX_DUTY_CYCLE 750`, one identifier away from
`PWM_MAX_DUTY_CYCLE 850` in `config.h` and with a different value. A safety
limit that shadows the global one by name is a genuine trap, and it violates the
project's own "tunable numbers live in `config.h`" rule.

### H4 — Unchecked array index in the PWM driver, reachable from an ISR (Rule 7)

[pwm.c:116](Src/drivers/pwm.c#L116):

```c
static const channel_hw_t *channel_hw(uint32_t channel) {
  return channel_hardware[channel];   // no bounds check
}
```

Every public PWM entry point funnels through this, including `pwm_OCP_fault()`
which runs in ISR context at priority 0. An out-of-range `channel` yields a wild
pointer, and the code then writes HRTIM registers through it — including the
fault-latch path, so a bad index inside the fault handler could disable the
wrong channel's outputs, or none.

`channel_telem.c` guards the identical pattern with `telem_is_valid()`
([channel_telem.c:69](Src/drivers/channel_telem.c#L69)), so the convention
already exists in the project. It simply is not applied in the safety-critical
driver.

Same class, elsewhere: `channel_by_id()` is written to return NULL for an
invalid id ([channel.c:24](Src/app/channel.c#L24)) and is then dereferenced
unguarded at [channel.c:37](Src/app/channel.c#L37) and
[channel_telem.c:81](Src/drivers/channel_telem.c#L81).

### H5 — A refused start reports success (Rule 7)

[mode_single_ch_cv.c:43](Src/app/modes/mode_single_ch_cv.c#L43):

```c
pwm_start(CHANNEL_A);
return MODE_INIT_OK;
```

`pwm_start()` returns `bool` and refuses if the channel is not `STOPPED` or if
a fault is latched — including the case where it detected a *live* fault during
the enable sequence and re-latched. The return is dropped, without even the
`(void)` cast used deliberately fourteen lines below, so `mode_begin()` reports
`MODE_INIT_OK`, `app.c` transitions to `ACTIVE`, and the PI loop runs against a
channel that never started. The integrator winds up against a bus it cannot
move, which is precisely the failure `README.md` describes `STARTING` as
existing to prevent.

Then `mode_single_ch_cv_service()` continues to call `pwm_set_duty_cycle()` on
that channel, so the commanded duty climbs to the clamp while stopped. If the
underlying fault later clears and anything starts the channel, it starts against
a wound-up integrator and a saturated duty request.

---

## Part 2 — Power of 10, rule by rule

| # | Rule | Status | Hazard link |
|---|---|---|---|
| 1 | Simple control flow — no `goto`, `setjmp`, recursion | **Pass** | — |
| 2 | All loops have a fixed upper bound | **Fail** | H2 |
| 3 | No dynamic memory after initialisation | **Pass** | — |
| 4 | No function longer than ~60 lines | **Partial** | — |
| 5 | Two or more assertions per function | **Fail** | H1, H4 |
| 6 | Declare data at the smallest possible scope | **Mostly pass** | — |
| 7 | Check every return value and every parameter | **Fail** | H1, H4, H5 |
| 8 | Limited preprocessor use | **Pass** | H3 (naming) |
| 9 | Restricted pointer use, no function pointers | **Pass today** | — |
| 10 | All warnings on, zero warnings, static analysis | **Fail** | H1 (would catch) |

### Rule 1 — Simple control flow (pass)

No `goto`, no `setjmp`/`longjmp`, no recursion anywhere in the hand-written
code. State machines are explicit `switch` statements over named enums
([app.c:56](Src/app/app.c#L56), [check.c:49](Src/app/check.c#L49),
[serial.c:114](Src/drivers/serial.c#L114),
[channel_telem.c:284](Src/drivers/channel_telem.c#L284)). This is the shape the
rule asks for.

### Rule 2 — Bounded loops (fail)

`Error_Handler()` is H2 above. Two more, both in the command path:

[serial.c:113](Src/drivers/serial.c#L113) — `while (rx_buffer_pop(&byte))` and
[command.c:74](Src/app/command.c#L74) — `while (serial_take_next_frame(&frame))`.
Both terminate only because the producer (a 115200-baud UART ISR) is slower than
the consumer. That is a timing argument, it is nowhere in the code, and it is
the assumption the rule exists to eliminate. A UART clocked faster, a stuck
ISR, or a future FDCAN transport draining into the same loop turns a bounded
service into an unbounded one — and `app_loop()` stopping means the state
machine stops, which means STOP and CLEAR_FAULT commands stop being read while
the stage keeps switching.

One line each, and the bound is already a compile-time constant:

```c
for (uint16_t i = 0U; (i < UART_RX_BUFFER_SIZE) && rx_buffer_pop(&byte); i++) {
```

### Rule 3 — No dynamic memory (pass)

No `malloc`, `calloc`, `free`, or `printf`-family call in any hand-written
file. All state is file-scope `static` or in the two shared models (`sys`,
`channel_a..e`). This is the rule the codebase follows best.

Worth making structural rather than incidental: the linker script still reserves
`_Min_Heap_Size = 0x200` and `Src/sysmem.c` still provides `_sbrk()`. Setting
the heap to zero turns "we do not call malloc" into "malloc cannot link".

### Rule 4 — Function length (partial)

[app_loop()](Src/app/app.c#L47) is 123 lines with a `switch` nested inside a
`switch`. Everything else is inside the limit — `telem_service()` and
`mode_single_ch_cv_service()` at 56 lines each are next.

The rule's purpose is that a function fit on a screen so it can be reviewed as a
unit. In a system where this function is what decides whether five converters
run, reviewability *is* a safety property — this is the function where a missed
transition means outputs that should be stopped are not. Lifting each state body
into a `static system_state_t state_active(bool entered)` leaves the loop as the
service list plus a dispatch, which is what `README.md` already describes it as.

### Rule 5 — Assertions (fail)

There are zero assertions in the project, and the generated layer is worse than
absent: `USE_FULL_ASSERT` is commented out
([stm32h7xx_hal_conf.h:238](Inc/stm32h7xx_hal_conf.h#L238)), so every HAL
`assert_param()` compiles to nothing — and if it were enabled,
`assert_failed()` ([main.c:269](Src/main.c#L269)) has an **empty body**, so a
failed assertion logs nothing and *returns into the failing call* with the
invalid parameter.

The project's own "this should never happen" channel is dead:

```c
volatile bool error_flag = false;   // app.c:22
error_flag = true;                  // check.c:81, app.c:158
```

Nothing reads `error_flag`. `app.c:158` is the case where the board reached
`RESET` and `HAL_NVIC_SystemReset()` did not reset it — the CPU is in an
unknown state with the stage possibly live, and the response is to set a flag
nobody reads and fall out of the `switch`.

My earlier draft said two-assertions-per-function was not worth adopting here.
That was wrong for this system. The correct target is not a count, it is that
**every assertion has a defined safe-state action**: stop all outputs, latch a
code, enter `SYSTEM_STATE_FAULTED`. On a board where the safe state is
well-defined and reachable in a few register writes, an assertion that fires
into a known-safe stop is strictly better than a branch that silently continues.

```c
#define FW_ASSERT(cond, code)  do { if (!(cond)) fw_fault(code); } while (0)
```

with `fw_fault()` doing the register-write stop, latching `code` where the
stream can report it, and forcing `FAULTED`. Both existing `error_flag` writes
become `FW_ASSERT` sites, and H1's missing validity check and H4's missing
bounds check become natural ones.

### Rule 6 — Smallest scope (mostly pass)

Good: `channel_hw_t` and `telem_hw_t` are private to their drivers, `telem_seq`
is a private struct, and there are no non-static globals apart from the two
deliberate shared models and `error_flag`.

Two the other way:

- [check.c:65-69](Src/app/check.c#L65) declares `int res1 .. res5` directly
  under a `case` label with no braces. They stay in scope for every following
  case, and a declaration immediately after a label is a compiler extension
  rather than portable C11.
- [led.c:50-52](Src/drivers/led.c#L50) — `cur_time`, `interval`, `toggle` are
  unprefixed file-scope statics in a file where every other symbol is `led_*`.

### Rule 7 — Check return values and parameters (fail)

H1, H4 and H5 are the hazardous instances. Two more worth naming:

**Silent, permanent loss of the command link.** `serial.c` assigns
`HAL_UART_Receive_IT()`'s status into `rx_status` at lines 104, 160 and 165 and
never reads it. One failed re-arm and reception stops forever with no
indication — and serial is the interface that carries `STOP`, `CLEAR_FAULT` and
`RESET`. `rx_dropped`, `frames_dropped` and `tx_dropped` are counted and equally
never read; `telem_error_count()`, `telem_sweep_age_ms()` and
`led_channel_flash_service()` are public and never called. The instinct to count
these is right and every readout is missing. [stream.c:53](Src/app/stream.c#L53)
already reserves an unused flags byte that is the obvious place for them.

**The pre-arm checklist passes vacuously.** `CHECK` is the gate the board must
clear before it can ever run, and it re-runs after every `CLEAR_FAULT`. Today
`CHECK_STATE_WAIT_SWEEP` is "implement later" and falls through
([check.c:58](Src/app/check.c#L58)), and `check_iind_calibration()` is a stub
that ignores its parameter and returns `true`
([check.c:28](Src/app/check.c#L28)). Only `check_flt_lines()` does real work, so
a board with ten dead INA228s and stale telemetry passes CHECK and enters
STANDBY ready to be armed. That is a known gap rather than an oversight —
`README.md` open question 2 asks whether a failed check should be fatal — but
the gap is currently open in the permissive direction.

### Rule 8 — Preprocessor (pass)

No token pasting, no variadic macros, no recursive macros, no function-like
macros in hand-written code. Conditional compilation appears only in generated
files, and include guards are consistent. The `MAX_DUTY_CYCLE` shadowing is
under H3.

### Rule 9 — Pointer use (pass today)

No function pointers, no more than one level of dereference, no pointer
arithmetic beyond array indexing. `hw->data->pwm.op_state` is member chaining
through a single hop each, which the rule permits.

Flag for later: `README.md` plans a `static const` table of `{begin, service}`
function pointers at around six modes. That is the Rule 9 boundary. A `const`
table in flash, never reassigned, is the defensible form — worth writing that
constraint down at the point the table is created, along with the requirement
that every entry be non-NULL at compile time.

### Rule 10 — Warnings and static analysis (fail)

[cmake/gcc-arm-none-eabi.cmake:29](cmake/gcc-arm-none-eabi.cmake#L29) sets
`-Wall` and nothing else. Missing: `-Wextra`, `-Werror`, `-Wconversion`,
`-Wshadow`, `-Wundef`, `-Wswitch-enum`, `-Wmissing-prototypes`. No static
analyser is in the loop at all.

`-Wextra -Werror` cannot go on the whole target — the HAL will not survive it —
but the user sources are already listed separately at
[CMakeLists.txt:45-68](CMakeLists.txt#L45):

```cmake
set_source_files_properties(${USER_SOURCES} PROPERTIES
    COMPILE_OPTIONS "-Wextra;-Wconversion;-Wshadow;-Wundef;-Werror")
```

`-Wconversion` alone flags the unchecked `float` → `uint16_t` narrowing at
[mode_single_ch_cv.c:78](Src/app/modes/mode_single_ch_cv.c#L78) — the cast at
the heart of H1, where a PI output is narrowed with no range check before
becoming a duty command.

`CMAKE_EXPORT_COMPILE_COMMANDS` is already `TRUE`, so `clang-tidy` needs only a
`.clang-tidy` file. `cppcheck --enable=all` over `Src/app` and `Src/drivers`
runs in seconds on 2.5k lines.

Credit where due: `-fstack-usage` **is** enabled and `.su` files are being
produced. Nothing consumes them, and `_Min_Stack_Size` is 1 KB
(`STM32H743xx_FLASH.ld:71`) with no worst-case figure computed. Summing the
`.su` entries along the deepest call chain is a half-hour job that turns an
assumption into a number — and stack overflow on this part means corrupting the
`sys` and `channel_x` structs that hold the fault latches.

---

## Part 3 — Other firmware conventions

### Watchdog — absent, and it is the mitigation for H2

Neither IWDG nor WWDG is enabled
([stm32h7xx_hal_conf.h:64](Inc/stm32h7xx_hal_conf.h#L64)).

The IWDG runs from the LSI and is independent of the core, so it fires **even
with interrupts disabled**. That makes it the one mitigation that covers the
`Error_Handler()` trap in H2: without it, that trap is permanent; with it, the
board resets, the HRTIM is reset with the peripheral, and the outputs stop. It
also covers the unbounded drain loops in Rule 2.

`README.md` states that "nothing bounds the loop period, so no design should
depend on a maximum pass duration". That is a defensible design stance, but it
is exactly what makes the timeout hard to pick — the two decisions are coupled.
Picking a loop-period budget and a watchdog timeout together is the work here,
and it belongs in the open-questions list. Note the design intent already exists
elsewhere: `TELEM_STEP_TIMEOUT_MS` shows the project is willing to bound a
service when the failure is understood.

### `volatile` and ISR sharing — genuinely well handled

`uart_rx_buffer` / `rx_head` / `rx_tail` are `volatile`; `telem_seq.xfer` is
`volatile` with a comment at
[channel_telem.c:156](Src/drivers/channel_telem.c#L156) stating exactly who
writes which value; `ovp_latched` / `ocp_latched` are `volatile` in the shared
structs; `__DMB()` barriers bracket the fault-clear sequences in `pwm.c`. The
single-producer / single-consumer ring in `serial.c` is correct.

Two assumptions to write down rather than change: the ring relies on 16-bit
aligned loads and stores being atomic on Cortex-M7, and on each index having
exactly one writer. Neither is stated, and the project's own rule is "if you
assume something, write the assumption down".

### Two doc-versus-code disagreements found on the way past

The project rule is to say so rather than silently trust either:

- **`ANALOG_PERIOD_MS`.** `hardware.md` states `analog.c` "polls this one input
  every `ANALOG_PERIOD_MS` (10 ms, 100 Hz)". `config.h:37` sets it to `1U`.
  The code is a 10× faster bus sample than the doc describes. The CV loop runs
  at `SAMPLE_TIME_MS 1`, so 1 ms is consistent with the control step and the
  doc is likely just stale — but this is the sample rate of the measurement H1
  is about, so it should be reconciled deliberately rather than assumed.
- **`.agents/README.md` command surface.** It documents
  `*_take_next_system_command()` as the transport drain call and says "no
  transport exists yet". `serial.c` is implemented and the actual call is
  `serial_take_next_frame()` returning a `transport_frame_t`
  ([serial.c:154](Src/drivers/serial.c#L154)). Cosmetic, but `README.md` is the
  file another model reads first.

### Two sources of truth for the fault pin map

[check.c:18-23](Src/app/check.c#L18) reads the five FLT pins as literals
(`GPIOA, GPIO_PIN_15`, …). The same mapping exists as `channel_hw_t.fault_port`
/ `.fault_pin` in [pwm.c:36-104](Src/drivers/pwm.c#L36) and again as a table in
`hardware.md`. Re-pinning a channel means editing two files, only one of which
is the driver, and a stale copy in `check.c` means the bring-up check reads the
wrong pin and passes a faulted channel. This contradicts the project's own "one
home for a number" rule, and here the number is a protection input.

### Type safety on channel ids

`CHANNEL_A..E` are `#define`s and every driver takes `uint32_t channel`, so the
compiler cannot catch a channel argument swapped with a duty or a frequency —
`pwm_set_duty_cycle(CHANNEL_A, 500)` and `pwm_set_duty_cycle(500, CHANNEL_A)`
both compile, and the second is H4's wild index. A
`typedef enum { CHANNEL_A = 0U, ... } channel_id_t` in the public signatures
costs nothing at runtime and makes it a build error.

Same argument for `NUM_COMMAND_SLOTS 7` ([command.c:9](Src/app/command.c#L9)),
a hand-maintained duplicate of the `system_commands_t` size — a trailing
`SYSTEM_COMMAND_COUNT` enumerator keeps them in step automatically, and the
bounds check at `command.c:67` then cannot drift from the enum it guards.

### Testability

No automated tests, deliberately and documented in `workflow.md`. Worth noting
that `pi.c` and the planned `perturb_observe.c` are pure functions of their
inputs with no HAL dependency — they compile and run on the host as they stand.
H1 is a control-loop failure that a five-line host test would have caught
(`pi_update()` with a zero measurement, assert the output does not saturate
before a validity gate is applied), and MPPT is exactly the kind of algorithm
where a bench run cannot cover the interesting cases.

### Things done well

Consistent `snake_case` with module prefixes; one `.c`/`.h` pair per module;
every HAL weak callback in one file (`interrupts.c`), routing only and no logic;
fixed-width integer types throughout; a documented interrupt-priority table with
its reasoning attached and nothing sitting at or above the fault vectors;
drivers publishing into one shared model rather than keeping private copies;
non-blocking services sequenced across loop passes instead of polling with
timeouts; and the lock-free register-write fault stop in `pwm.c` with the
`__HAL_LOCK` reasoning recorded next to it.

The comment discipline — spending words on the arithmetic behind a number and on
what breaks if two calls are reordered — is better than most firmware of this
size, and `hardware.md` carrying the derivation for every limit is what made
this review possible at all. The gap being flagged throughout is not that the
reasoning is absent; it is that in several places the reasoning is recorded and
the corresponding check is not written.

---

## Part 4 — Prioritised fix list

**Before the board is next run with a battery on the output:**

1. **H1 — gate the CV loop on measurement validity.** Add a validity flag
   alongside `sys.vbus_mv`, set false on any failed conversion, and refuse to
   run the PI update without it. Add a plausibility check: if the measurement
   is implausibly far from the setpoint for N consecutive passes, fault rather
   than integrate.
2. **H2 — stop the outputs in `Error_Handler()`** before `__disable_irq()`,
   using the register-write stop. Whole body is inside USER CODE markers.
3. **Enable the IWDG.** The only mitigation that survives interrupts being
   masked, and it needs a loop-period budget decided alongside it.

**Before a second mode exists:**

4. **H3 — build the duty gate in `control.c`** and make it the only path to
   `pwm_set_duty_cycle()`, so slew limiting cannot be forgotten by a new mode.
   Scale it by operating point; `hardware.md` says the bench constant is ~6×
   too permissive on the array.
5. **H4 — bounds-check `channel_hw()`**, and give `channel_by_id()`'s NULL
   return a checked caller or remove the NULL case.
6. **H5 — check `pwm_start()`'s return** in `mode_single_ch_cv_begin()`.

**Structural, cheap, do it alongside:**

7. **Scope `-Wextra -Wconversion -Werror` to the user sources**
   (`CMakeLists.txt:45`). One call, and it turns the next instance of items 1,
   5 and 6 into a build failure instead of a review finding.
8. **Add `FW_ASSERT` with a defined safe-state action** and convert the two
   dead `error_flag` writes to use it.
9. **Bound the two drain loops** in `serial.c` and `command.c`.
10. **Surface the dropped-frame and telemetry-error counters** through
    `stream.c`'s unused flags byte.

---

Nothing in this report has been compiled or run — it is a read of the source at
`99f8748` against `.agents/hardware.md` and `.agents/README.md`. **No code
changes have been made.** H1 in particular is a code-versus-documentation
disagreement rather than an observed failure; it should be confirmed on the
bench (force a failed conversion and watch the duty) before being treated as
established.
