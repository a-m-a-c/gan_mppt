# Measurement 5: accepted MPPT capture

Accepted run: 20261030-220906-265369_measure_mppt_profile (30 October 2026, 22:09 Australia/Sydney).
Top-level measure.csv/.bin/.json, profile files and measure_mppt.csv are the accepted run, unchanged.
Settings: channel A, 600 kHz, 70% maximum duty, 100 mV P&O step, sustained settled-power averaging and fractional PI accumulation. Source 0.5-8 A in 0.5 A steps with 30 s holds; 35 V source limit.
Complete, supply off confirmed. 220 missing measurement records, 33 CRC errors, 2 missing diagnostic records; no firmware acquisition errors or queue drops.

## Preserved iterations

- iterations/01_original_50mv: former captures/measure_5; 50 mV staircase.
- iterations/02_settled_100mv_before_pi_fix: former 20261030-213538-609541_measure_mppt_profile; 100 mV, settled averaging, before fractional PI correction.
- comparison: earlier figures/statistics plus accepted-run overlay mppt_pi_fixed_overlay.png/.svg.

The 20:38 gradual-ramp and 21:13 100 mV low-current raw folders were already absent when this archive was prepared. Their surviving comparison figures/statistics are included; no missing raw data was reconstructed.
Original acquisition JSON keeps its original folder paths as provenance. Consult this index for current locations. Comparison JSON/scripts use repository-relative current paths. Plot scripts are intended to run from captures/comparison in the repository; archived copies are provenance copies.
The separate IV reference is measure_23_new, attached to Measurement 23. Separate-run source/temperature drift limits tracking-efficiency claims.

## Five-minute continuous-ramp capture

`20261031-111407-776522_measure_mppt_profile/` contains the additional 31 October 2026, 11:14 run: source current 0.5-8 A over 300 seconds, followed by a 5-second final hold. The staircase remains at the top level; timestamps and CSV rows from the two runs are not concatenated.

The ramp completed, with 3 missing measurement records, 2 CRC errors, no diagnostic gaps and no firmware errors/drops. Its `comparison/mppt_ramp_overlay.png` and `.svg` compare the ramp with the IV reference and accepted staircase. All 15 files were SHA-256 verified during the move; the offline plotting helper was then adjusted for its new directory depth. Acquisition manifests retain their original folder paths as provenance.

The previously exported ZIP and Notion attachments predate this addition and do not yet include the ramp run.
