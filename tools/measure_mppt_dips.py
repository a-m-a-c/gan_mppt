"""Five 0.5 s dips from 8 A to 2 A. Capture enables source and channel A MPPT."""
import argparse
import json
from pathlib import Path
from current_profile import CurrentStep, validate_profile
from measure_5_common import ROOT, reference_rows, plot
import measure_mppt_profile as runner
from sps5162x import SPS5162X


def build_profile():
    steps = [CurrentStep(0, 8)]
    for n in range(5):
        steps.extend([CurrentStep(10 + n, 2), CurrentStep(10.5 + n, 8)])
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port")
    mode.add_argument("--replay", type=Path)
    parser.add_argument("--resource")
    parser.add_argument("--reference", type=Path, default=ROOT/"captures/measure_iv_curves")
    parser.add_argument("--output", type=Path, default=ROOT/"captures/measure_mppt_dips")
    args = parser.parse_args()
    try:
        _, peaks = reference_rows(args.reference)
        for current in (2, 8):
            if len([p for p in peaks if float(p["source_current_a"]) == current]) != 1:
                raise ValueError(f"Need one usable reference MPP at {current} A")
        steps = build_profile()
        validate_profile(steps, 35, 35, 8, 10, .5)
        folder = args.replay
        if folder is None:
            with SPS5162X.open(args.resource) as supply:
                result = runner.capture(supply, args.port, steps, output_dir=args.output,
                                        final_hold_s=10, make_plots=False)
            folder = Path(result["folder"])
            print(f"Complete: {result['complete']}; supply OFF confirmed: {result['output_off_confirmed']}")
            for key in ("error", "uart_error", "shutdown_error"):
                if key in result:
                    print(f"{key}: {result[key]}")
        plot(folder, "timescale", args.reference, 10,
             time_title="MPPT disturbance response: five 0.5 s dips from 8 A to 2 A")
        print("Holds use host scheduling; actual command/verification times are in profile.csv.")
        return int(not json.loads((folder/"run.json").read_text())["complete"])
    except Exception as error:
        print(f"Dip capture failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
