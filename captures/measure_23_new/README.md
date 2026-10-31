# Combined reference I-V characterization

16 completed sweeps: 0.5 to 8 A in 0.5 A steps.

Mixed settings: 0.5-4.5 A used 50 ms holds; 5-8 A used 100 ms. Final 6.5-8 A run removed the reverse-current cutoff. All requested 600 kHz, upward only to 70%. Original folders preserved.

Per-sweep files are unchanged copies, verified by SHA-256. Source manifests are under source_runs; run.json maps new indices to originals. Failed 5 A and 6.5 A attempts are archived under failed_attempts and excluded from combined data and plots.

Derived files: all_samples.csv, settled_points.csv, reference_mpp.csv, iv_curves.svg, iv_curves.png and analysis.json. Data-quality warnings remain visible.

MPPT overlays and comparison statistics have moved to `../comparison/`; see its README for the capture identities. `iv_curves.*` here now contains only the reference sweeps.
