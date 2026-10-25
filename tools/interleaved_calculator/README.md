# Interleaved boost calculator

Run from the repository root:

```powershell
uv run --project tools --locked python -m tools.interleaved_calculator.calculator
```

The script prints per-channel values, searches phase offsets, then opens two
Matplotlib windows. Close both windows to exit. The settings and panel instances
are in `calculator.py`'s `main()`.

- **Comparison figure:** bus-voltage waveforms, a zoom of the lower-ripple cases,
  capacitor-current waveforms, and peak-to-peak ripple bars.
- **Timing figure:** each channel's output-current delivery intervals for aligned,
  72-degree-spaced and optimised phases. Phase labels refer to low-side turn-on.

PNG/SVG figures and full-precision `phase_results.json` are saved in the adjacent
`out/` directory (ignored by Git). To generate them without opening windows:

```powershell
uv run --project tools --locked python -m tools.interleaved_calculator.calculator --no-show
```

## Phase search

`phase_optimisation.py` minimises peak-to-peak bus ripple, using exact event
integration from `bus_ripple.py`. F1 stays at zero because a common rotation of
all phases does not change ripple. The other four angles can move independently
through a full cycle; their order is not constrained to panel order.

Four differential-evolution searches use seeds 7, 23, 61 and 109, population size
20 per free angle, and up to 1200 generations. The best result is retained along
with the aligned and equally spaced baselines. Multiple seeds improve coverage;
they do not prove global optimality. Search results are printed to expose cases
where runs settle at different minima. This is a continuous-angle calculation,
not a calculation of implementable timer ticks.

At the current defaults, the best found ripple is approximately **1.838 mV pp**,
versus **12.106 mV pp** for 72-degree spacing and **101.072 mV pp** aligned.
Use the saved full-precision angles for reproduction; several phase assignments
can have the same ripple, and the printed angles are rounded.

## Model limits

The comparison holds power, frequency and capacitance fixed. It assumes rectangular
CCM output-current pulses, constant load current and five directly parallel
48.84 uF capacitors (244.2 uF total). It excludes inductor ripple, capacitor
ESR/ESL, isolation and battery impedance. These effects can be material compared
with the small predicted optimised ripple. This is not a hardware performance claim.

See `bus_ripple_math.tex` for the original derivation. The phase search uses the
same equations with independently adjustable phases. In mathematical terms:

```text
minimise max_t(vbus(t)) - min_t(vbus(t))
over phases BL, BR, R1, R2 in [0, 360) degrees, with F1 = 0 degrees.
```

Verification:

```powershell
uv run --project tools --locked python -m unittest tools.tests.test_bus_ripple -v
```

The tests include analytical cancellation, phase rotation/wraparound, independent
time-sampled integration, charge balance and a known zero-ripple optimisation case.
