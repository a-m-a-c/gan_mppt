# PI-only input-voltage response

Build and flash the new firmware before capture. Disconnect the bench GUI's
serial connection. From the repository root:

```powershell
uv run --project tools --locked python tools/measure_pi_response.py --port COM6 --current 5 --targets 25 30 25 --hold 3
```

This enables the SPS5162X source and channel A. The source current limit stays
at 5 A, with 35 V compliance, while the firmware controls **input voltage** at
25, 30 and 25 V. This assumes the existing characterised panel/source and bus
arrangement; a stiff supply held at 35 V is not a substitute for that source.
The current limit is not a promise of constant measured input current.

The default hold is 3 s. The first hold begins at the first valid switching
sample, after gated startup (2 s voltage stability). Later holds begin at
the target acknowledgement; each includes the voltage transition. Each target must
be acknowledged within 2 s. The supply stays off until the initial reference
has been acknowledged. The script rejects firmware without the PI mode flag.
The initial 2 s baseline is supply-off. At normal completion the host sends STOP
and waits for a clean END with PWM inactive before switching the supply off.
Shutdown waits for the final target hold to finish. There is no supply-off recording tail for PI tests. If END is missing or faulted,
the bounded wait fails the capture and the supply is still switched off. Output-off
request timing is saved separately; inactive samples are excluded from the plots.

## Firmware

`mode_pi_test.c` uses the existing `pi.c` directly. P&O is not called.
Current trial gains are Kp=0.002, Ki=0.3, using millivolts and duty units (1000=100%).
Gains increased from Kp=0.00144, Ki=0.288 for the next 25 -> 30 -> 25 V trial.
Edit those values in `pi_init` and rebuild/reflash for a gain comparison.
The script records the expected defaults as a note, not as firmware readback.

- Channel A, 600 kHz (480 MHz / 600 kHz = 800 timer ticks), 40 ms control period.
- Fractional integral retained unless applied duty differs from the request.
- Duty limited to 0..700 (70%), with the existing 1 unit/ms ramp limiter.
- Startup uses zero duty; the first hold includes the resulting ramp.
- Targets: 2..30.5 V, and at least 0.30*Vbus + 0.5 V. The 0.5 V margin
  follows the existing measurement-mode bench assumption. Startup also waits
  for measured input voltage to reach the requested initial target.
- Retains hardware fault handling; fails on invalid/nonfinite telemetry,
  telemetry older than 10 ms, Vin >35 V, Vout/bus >50 V, Iin >9.5 A,
  Iout >8 A, or either current below -10 A. Running Vin <4 V also stops it.
  These are conservative bench-test limits, not newly established board ratings.
- Firmware stops after 180 s; the host restricts target holds to <=150 s total
  to leave startup/setup/shutdown margin. STOP restores the entry frequency.

Protocol: `pi_test` 0x12 with no payload; `pi_target` 0x13 with uint16
little-endian millivolts; existing `measure_start` 0x11 releases startup.
Target commands are accepted only while this mode owns acquisition and must
pass the bounds check. STOP outranks target changes.
Non-IV MPM1 bit 11 identifies PI-test capture. Existing MPD1 records carry
target, Vin-target error, instantaneous input power and MCU timestamp;
UPDATED marks reference application and `decisions` counts reference changes.
These records are PI diagnostics, not P&O decisions or MPP estimates.

## Results and replay

The timestamped capture folder contains the existing raw measurement files and
`measure_mppt.csv` (the reused diagnostic format), plus:

- `pi_test.json`: requested sequence, host request/ack times, MCU application
  times and source-off request boundary.
- `pi_response.json`: voltage settling, directional overshoot on steps,
  final-window mean error/standard deviation, completeness and data quality.
- `pi_response.png/.svg`: voltage with applied target, duty and input power.

```powershell
uv run --project tools --locked python tools/measure_pi_response.py --replay captures/<folder> --band 0.1 --dwell 0.5
```

Replay opens no hardware. It replaces only derived `pi_response.*` products.
Metrics use MCU sample and target-application timestamps. Startup acquisition
begins at first switching; later steps begin at reference application. Settling
is the first continuous dwell within an absolute voltage band, not proof that
all later samples remain inside. Defaults +/-0.1 V and 0.5 s are configurable
analysis choices. Gaps over 10 ms, sequence gaps and invalid samples reset dwell.
Use tail mean error and variation alongside settling to judge the loop. Tail
statistics cover up to the final 2 s of each phase. Raw files survive failed runs.

Verified with host firmware harness, simulated supply/UART capture and offline
analysis. Debug and Release compile. Not flashed or verified on powered hardware.
