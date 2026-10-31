# MPPT algorithm comparisons

Latest accepted result: mppt_pi_fixed_overlay.png / .svg. Green is the accepted fractional-PI run, orange is the preceding settled-averaging run, black is the measured reference MPP path, and blue is the original 50 mV run.

Current source folders:
- ../measure_5: accepted 22:09 fractional-PI run.
- ../measure_5/iterations/01_original_50mv: original measure_5.
- ../measure_5/iterations/02_settled_100mv_before_pi_fix: 21:35 settled-averaging run.
- ../measure_23_new: combined IV reference.

Other preserved figures: mppt_settled_overlay (before PI fix), mppt_100mv_comparison, trajectory_comparison and 50mv_ramp_overlay. The 20:38 gradual-ramp and 21:13 low-current raw folders were already absent at archive preparation; these figures/statistics retain that history.

CSV/JSON contain source paths and mean windows. Historical captions use original capture labels. Paths are relative to repository root. Different hold durations, source drift and temperature mean these are not calibrated tracking-efficiency comparisons.
