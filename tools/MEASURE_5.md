# Measurement 5 capture scripts

Run one at a time after flashing the current MPPT gains and disconnecting the GUI from COM6.
These commands enable the source and channel A using the existing measurement runner.

```powershell
uv run --project tools --locked python tools/measure_5_continuous.py --port COM6
uv run --project tools --locked python tools/measure_5_staircase.py --port COM6
uv run --project tools --locked python tools/measure_5_timescale.py --port COM6
```

- Continuous: 0.5 to 8 A over 600 s, 0.5 s programming interval, then 5 s at 8 A. The supply uses 1 mA quantization: this is a fine digital ramp, not an analogue ramp.
- Staircase: 0.5, 1, ... 8 A, 30 s each (16 * 30 = 480 s).
- Timescale: 1, 2, ... 8 A, 10 s each (8 * 10 = 80 s).

These are source-profile durations; the first hold includes gated PWM startup. The runner adds 2 s baseline and 2 s shutdown tail plus setup time. Compliance is 35 V; firmware uses measurement mode, not PI-only mode. Existing shutdown and fault handling are reused.

Outputs go to captures/measure_5_redo/<kind>/<timestamp>_measure_mppt_profile/. Raw records are retained. comparison/ contains SVG, PNG, averaged CSV and metadata JSON.
The first two plots show the measured VI trajectory against the up-branch IV curves and usable MPP path from captures/measure_iv_curves. Override with --reference FOLDER. Separate-run reference drift applies; no tracking-efficiency claim is made.
The third plots commanded source current, measured input voltage, input current and input power against host time. Dashed lines show the matching up-branch IV-reference MPP voltage, current and power at each source-current step, without interpolation. Host command/UART timing is approximate, not ADC-synchronized; separate-run source/temperature drift applies.

All plots default to non-overlapping 10-sample averages. Invalid/inactive samples and sequence/time gaps reset averaging; incomplete groups are discarded. Power is averaged after multiplying each raw voltage/current pair. --average-samples 1 retains individual valid active samples.

Offline replotting (no hardware access): replace --port COM6 with --replay CAPTURE_FOLDER using the matching script. Existing raw data is never rewritten. Titles describe the selected script's profile, so replay the matching run type.

Verification: all three profile schedules checked for endpoints, count and duration; all three plot paths rendered with existing capture data. No hardware run performed by the agent.
