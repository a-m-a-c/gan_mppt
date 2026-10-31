# Reference I–V curves for measurement 5

`characterise_iv.py` holds the SPS5162X at each selected current limit and
repeats the same firmware sweep used by `measure_iv_sweep.py`. It imports
the existing recorder and settled-point extraction; it does not run MPPT.
No firmware changes are required by this script.

## Before running

- Use channel A with the **dark 39-cell panel in parallel with the supply**,
  and the existing 12S4P 10 Ah battery load. This characterizes the combined
  bench source, not an illuminated panel's STC curve.
- The board must already have the current upward-only IV-sweep firmware flashed:
  600 kHz, 0% → 70%, 0.1 percentage point every 100 ms, then PWM off after
  holding 70% for 100 ms. There is no downward sweep.
  The script requires the duty-sweep protocol markers. Frequency is
  not in the wire format, so the saved requested frequency is not a readback.
  **Reflash for the 100 ms upward-only 70% sweep before running.** The required
  markers alone do not verify the endpoint or timing; older 50 ms firmware will
  still run faster even though the host records the requested 100 ms setup.
- Use the existing bench limits and scope checks. The battery must be within
  the firmware's 35 V to below 50 V window. This test is not a battery charger.
- The IV sweep permits finite reverse input/output current at the user's request;
  its former -10 A software cutoff is removed. Positive limits remain 9.5 A input
  and 8 A output, and the PWM-off startup check still requires both current
  magnitudes <=0.5 A. Hardware-fault handling and voltage/telemetry checks remain.
  This is not a claim that the hardware comparator provides reverse-current
  protection or that the capacitance/inrush explanation has been verified.
- Connect the supply USB and MCU UART. Disconnect the GUI/console from the
  serial port. Only one process can own it.
- Record panel temperature and setup/firmware revision in `--notes`. Keep the
  conditions comparable to measurement 5. The dark panel heats when absorbing
  supply current, including during the PWM-off intervals.

From the repository root, install/sync the locked dependencies if needed:

```powershell
uv sync --project tools --locked
```

## Live capture

**This command enables the real power supply and starts switching channel A.**
Replace COM6 with the MCU's port.

```powershell
uv run --project tools --locked python tools/characterise_iv.py --port COM6 --notes "Dark panel; temperature ...; board/firmware ..."
```

Defaults: **0.5, 1.0, 1.5, …, 8 A**, 35 V compliance, a **5-second supply-OFF
pause before every sweep (including the first)**, 2 seconds supply settling
before each sweep, and a 95-second per-sweep host timeout. Each firmware
sweep takes about 72.6 seconds. Total nominal time is about
16 × (72.6 + 5 + 2 + 0.5) = 1281.6 seconds, or **22 minutes**, plus instrument and
plotting overhead. Allow storage for several million raw records.

To start with a shorter selection:

```powershell
uv run --project tools --locked python tools/characterise_iv.py --port COM6 --currents 0.5 2 4 6 8
```

`--currents` accepts distinct levels greater than zero and at most 8 A, in
0.001 A increments. They run in the supplied order. For another repeat, run
the command again; it creates a new timestamped folder. To investigate thermal
drift, repeat a selection in reverse order and compare the references.

Optional arguments:

| Argument | Meaning |
|---|---|
| `--settle 5` | Wait 5 seconds after supply enable before each sweep; this is a delay, not an automatic thermal/voltage settling test |
| `--off-seconds 5` | Keep the supply off for 5 seconds before every sweep; default 5; distinct from powered settling |
| `--timeout 95` | Host deadline for each sweep, in seconds; maximum 300; firmware deadline is 85 seconds |
| `--resource "USB0::...::INSTR"` | Explicit VISA resource if automatic discovery is ambiguous |
| `--output captures` | Parent directory for a new timestamped run |
| `--trajectory captures/measure_5/measure.csv` | Overlay a measurement-5 CSV on the resulting V–I plot |
| `--notes "..."` | Preserve test conditions in `run.json` |

The script first switches the source off and sends MCU STOP. For each level
it waits the supply-off interval, programs the limit with output off, enables the source, waits, verifies CC
and the settings, then invokes the existing IV recorder. A separate thread
logs supply readbacks approximately once per second without blocking UART
capture. Supply queries are sequential and not synchronized ADC samples.
The source switches off between sweeps and at exit. Current/voltage setpoints
remain at the last programmed values with output off.
The off pause provides recovery time; it does not measure or guarantee capacitor
discharge. A bus still connected to the battery will remain battery-supported.

To collect the remaining levels after a batch completed through 4.5 A:

```powershell
uv run --project tools --locked python tools/characterise_iv.py --port COM6 --currents 5 5.5 6 6.5 7 7.5 8 --off-seconds 5
```

This creates a new folder; retain the earlier batch for the 0.5–4.5 A curves.

Ctrl+C requests shutdown and preserves partial data. A source-check failure,
MCU fault, missing completion marker, interruption or acquisition error stops
the sequence. CRC errors, sequence gaps and firmware queue drops are separately
reported data-quality warnings; a normally completed sweep proceeds to the next
level. Whole duty visits bordering a sequence gap are excluded from settled
points and MPP extraction, including both visits when the gap crosses a duty
transition. Remaining reference candidates still require review for coverage
and uncertainty; a completed run does not mean its data are clean. The recorder sends
MCU STOP on exit. The final manifest reports whether supply OFF was confirmed;
an unconfirmed shutdown is a failed run. Host monitoring is not a replacement
for firmware/hardware protection, and cannot guarantee shutdown after USB or
host failure.

## Output

Everything is under `captures/<timestamp>_characterise_iv/`:

| File | Contents |
|---|---|
| `all_samples.csv` | Every raw MCU CSV record, including invalid/terminal records, with sweep index, source-current setting and sweep-completion flag |
| `settled_points.csv` | Per-duty-visit averages and measured voltage/current ranges; older up/down recordings retain both branches |
| `reference_mpp.csv` | Maximum settled input-power point for each sweep branch, with `peak_bracketed` and `reference_usable` flags |
| `iv_curves.svg`, `iv_curves.png` | All curves on a common **current-horizontal, voltage-vertical** plane, plus a P–V panel and usable reference MPP path |
| `run.json` | Requested setup, instrument identity, notes, per-sweep completion, errors and OFF confirmation |
| `analysis.json` | Averaging rules, reference counts, overlay path and timebase explanation |
| `sweep_NN/measure_iv_sweep.csv`, `.bin`, `.json` | Original recorder files for each current level |
| `sweep_NN/source.csv` | Supply setpoint/CC/output-state readbacks and host query timestamps |

Raw `t_s` and `t_host_s` restart for every sweep; use `sweep_index` to separate
them. The individual recorder JSON stores the host monotonic origin; the supply
log records absolute host monotonic query start/end times. Keep the per-sweep
metadata with the combined CSV.

Settled points first exclude whole duty visits affected by missing records,
then use the existing extractor: discard the first 20 ms of each
contiguous duty visit, require at least 40 ms of recorded hold and ten retained
samples, and average paired `V*I`. The 40 ms minimum preserves support for older
50 ms captures; current 100 ms holds leave approximately 80 ms after the exclusion.
These are analysis assumptions, not proof
that the source has settled in 20 ms. Review raw traces and repeatability.
A maximum is bracketed only if lower-power points were measured at
both higher and lower voltages. An endpoint maximum or any failed sweep is
excluded from the reference path. Peaks are measured candidates, not a fitted
or independently calibrated ground truth; no Isc endpoint is inferred.

## Offline plotting and measurement-5 comparison

These commands do **not** open the serial port or power supply. They overwrite
derived CSVs/plots in the given run folder but preserve original captures.
Replotting also reassesses older runs rejected solely by the former strict
data-quality check. Per-sweep assessments appear in `analysis.json`; the original
`run.json` is preserved, and a batch that stopped early remains partial.

```powershell
uv run --project tools --locked python tools/characterise_iv.py --replot captures/<timestamp>_characterise_iv
uv run --project tools --locked python tools/characterise_iv.py --replot captures/<timestamp>_characterise_iv --trajectory captures/measure_5/measure.csv
```

Replace the example paths with actual folders. The overlay uses all valid,
switching, non-faulted channel-A records for selection, with at most 20,000
displayed points. All characterization raw records remain in the CSV.
The curve colours identify **supply settings**, not converter input current.
Solid curves are increasing duty; older recordings with a downward pass retain
their dashed curves when replotted.

A measurement-5 trajectory on an I–V curve is physically consistent with that
source condition, but does not by itself demonstrate MPP tracking. Compare its
settled operating points with the corresponding supply-setting MPP reference.
Disagreement between repeated sweeps indicates drift, settling or measurement uncertainty that
must be resolved before treating one path as a reference. This script supplies
the overlay and reference table; it does not automatically assign trajectory
samples to source plateaus or calculate tracking efficiency. Continuous ramps
require interpolation and time alignment using the measurement-5 source log.

## Mock tests only

```powershell
uv run --project tools --locked python -m unittest discover -s tools/tests -p test_characterise_iv.py -v
```

The tests mock both serial and supply access, forbid real VISA connections,
and exercise the real UART decoder/recorder and plotting. They cover repeated
sweeps, transition-spike rejection, unbracketed maxima, faults, corrupt data,
source failures, cancellation and failed shutdown. Passing these tests is
software verification only; the new orchestration has not been bench-tested.
