# Measurement 24: MPPT acquisition and disturbance response

Run from the repository root. Capture enables the SPS5162X output and channel A
MPPT. Disconnect the bench GUI from the serial port first. Use the existing
characterised panel/source/load arrangement; changing a stiff supply's current
limit alone does not establish a PV reference curve.

Example (choose currents appropriate to the connected setup):

```powershell
uv run --project tools --locked python tools/measure_mppt_response.py --port COM6 --high 4 --low 2 --hold 30 --reference captures/measure_23_new/reference_mpp.csv
```

Sequence: 2 s supply-off baseline, startup at the high current setting, decrease
after 30 s, recovery after another 30 s, 30 s final hold, supply off and 2 s tail.
The first hold includes startup. `--hold` changes all three plateau durations.
The initial startup gate, fault handling, raw UART recording and shutdown paths
are reused from `measure_mppt_profile.capture`. Firmware settings are unchanged.
Source compliance defaults to 35 V; this is not the output bus voltage.
The existing source limits remain 35 V / 8 A. A failed capture retains partial data.

Offline analysis (no serial/VISA connection is opened):

```powershell
uv run --project tools --locked python tools/measure_mppt_response.py --replay captures/<run-folder> --reference captures/measure_23_new/reference_mpp.csv --tolerance-pct 2 --dwell 1
```

The reference is optional. It must have `source_current_a,pin_w` columns;
`reference_usable=False` rows are excluded. Only exact current matches are used.
Check that the reference describes the same panel, source, temperature and setup.
The historical reference is an example, not automatically a valid calibration
for a new run. Without a matching reference, power plots and tail statistics are
available, but MPP settling/acquisition times remain unresolved.

Outputs in the capture folder:

- Original `measure.csv/.bin/.json`, `measure_mppt.csv`, `profile.csv`,
  `summary.json` and `run.json` from the existing runner.
- `response.csv`: input power, validity, gaps, host and MCU timestamps.
- `response.json`: phase boundaries, reference values, timing definitions,
  acquisition/settling results, tail power mean/standard deviation/peak-to-peak,
  and capture completeness/data-quality information.
- `response.svg` and `response.png`: input power, reference plateaus and events.

Re-analysis replaces only the four `response.*` products, not raw captures.
Source command-to-verification intervals are shaded. Samples use UART arrival
time on the combined plot, not MCU sample time: these are approximate response
times, not synchronised ADC-to-source latencies. MCU timestamps remain in the
raw data and `response.csv`. The startup metric begins at the MPPT start command;
subsequent metrics begin at each source command.

Settling means the first entry into the +/- tolerance band that lasts for the
specified dwell. It does not mean the signal stays inside forever. Raw valid
switching samples are used without smoothing. Invalid samples, sequence gaps,
or intervals over `--max-gap` (default 0.05 s) reset the dwell. Defaults of 2%,
1 s dwell and a 5 s tail window are analysis choices, not performance targets.
Tail statistics describe the final `--steady-window` seconds, not proof of a
steady state. Partial captures are labelled explicitly; inspect their quality
flags before using metrics in the poster. Separate-run drift limits reference
accuracy; the script does not claim calibrated tracking efficiency.

Verification: synthetic response/gap tests and offline plotting; no powered
hardware verification of this script.
