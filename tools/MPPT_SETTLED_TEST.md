# Settled-power MPPT staircase test

Measurement mode uses a 100 mV P&O step, paired V*I averaging, and a sustained voltage-settling gate. The other MPPT modes retain their original policy.

After each perturbation, discard the first 20 ms. Collect at most one fresh observation per millisecond, requiring voltage within +/-75 mV of target and a window span no greater than 100 mV peak-to-peak. A decision needs at least 30 ms and 20 observations, with no observation gap over 3 ms. Invalid readings or excursions restart the window. Average all accepted paired V*I readings in the window. The window can exceed 30 ms while waiting for the existing 100 ms minimum P&O interval and 40 ms control service. A timeout never overrides settling in measurement mode. If settling never passes, the target stays fixed while PI continues regulating.

The settling thresholds are bench assumptions to evaluate in this test. PI gains, 40 ms control cadence, 600 kHz switching and 70% maximum duty retain their previous measurement values.

Measurement mode now preserves fractional PI accumulation. It initializes the integral from the startup duty and subsequently tracks applied duty only when it differs from the previous integer request (for example, while a ramp limiter intervenes). PI output clamping/anti-windup remain active. Other MPPT modes retain their previous policy.

Each control update also logs an MPD1 diagnostic alongside the unchanged MPM1 measurement records. The recorder saves diagnostics separately in `measure_mppt.csv`, keeping `measure.csv` compatible with the existing plots. The binary file contains both packet types. Diagnostics cost 46 bytes * 25 Hz = 1150 bytes/s and never block control; their queue policy reserves two slots for acquisition/final records.

## Reading the diagnostics

- `target_mv`, `error_mv`: current target and measured Vin minus that target, after this update's P&O decision.
- `updated`, `decisions`: whether this update accepted a perturbation, and its cumulative count since MPPT startup/replug.
- `power_mw`, `compared_target_mv`: last accepted mean(V*I) and the target at which it was measured. These remain unchanged between decisions and are blank before the first decision.
- `direction`: next search direction, +1 for increasing target voltage, -1 for decreasing it.
- `age_ms`: time since the latest decision, or since startup if none yet.
- `settled`, `window_samples`: settling gate result and window count before this update consumes/resets the window. A true gate still needs the minimum P&O interval.
- `pi_tracking`: true when this update resynchronized PI to applied duty.
- `sequence_gap`, `dropped`: diagnostic transport gaps and cumulative firmware diagnostic queue drops. These have separate counters from measurement samples; a missing diagnostic is not a missing ADC sample.

MCU `t_s` shares the acquisition clock; `t_host_s` is host reception time. The status is a control-update snapshot, not a simultaneous ADC sample. A fixed target, increasing age and no decisions identify a waiting P&O loop; use error/window count to investigate why. The recorder prints the diagnostic count at completion. Zero records with an active capture suggests old firmware or lost diagnostic transport.

MPD1 wire format: 46 bytes, little endian; CRC-16/CCITT-FALSE over bytes 0-43, stored at 44. Offsets: magic 0; uint32 sequence 4, time_us 8, target_mv 12; int32 error_mv 16, power_mw 20; uint32 age_ms 24, decisions 28; uint16 window_samples 32, flags 34; uint32 compared_target_mv 36, diagnostic dropped 40. Flag bits 0-4 mean updated, power valid, settled, PI tracking, direction positive; bits 8-10 contain channel ID. Diagnostic sequence is independent of MPM1 sequence and resets on capture entry.

## Run

1. Leave the source output off and disconnect the GUI/console from COM6.
2. In VS Code, use **Ctrl+Shift+B**, **Build & Flash Release**. Wait for successful programming/verification and STANDBY.
3. From the repository root, run:

```powershell
uv run --project tools --locked python tools/measure_mppt_profile.py --port COM6 --profile tools/profiles/mppt_staircase_30s.csv --final-hold 30
```

This explicitly selects 0.5, 1.0, ... 8.0 A. Each source current holds 30 seconds, including the final level: 16*30=480 seconds, plus the existing off baseline/tail. Startup settling consumes part of the first source hold. The script controls the supply and stops at completion; do not enable it manually. The voltage setting remains 35 V. New timestamped captures preserve old runs. Check `Complete`, `Supply OFF confirmed: True`, `MCU END received: True`, and reported data-quality warnings after the run.

Existing comparison plots/data are in `captures/comparison`, with source identities in its README. Raw captures and the combined reference are preserved.

Debug and Release compile; controller and capture checks use mocked hardware. No flashing or powered testing was performed by the agent.
