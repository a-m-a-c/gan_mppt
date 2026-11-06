"""8 A MPPT startup capture and offline transient plot. Capture enables source and channel A."""
import argparse
import csv
import json
import math
from pathlib import Path
import numpy as np
from current_profile import CurrentStep
from measure_5_common import ROOT, reference_rows
import measure_mppt_profile as runner
from sps5162x import SPS5162X


def plot_startup(folder, reference, before=.2, after=1):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _, peaks = reference_rows(reference)
    matches = [p for p in peaks if float(p["source_current_a"]) == 8]
    if len(matches) != 1:
        raise ValueError("Need one usable 8 A reference MPP")
    peak = matches[0]
    run = json.loads((folder/"run.json").read_text())
    start = None
    raw, averaged, batch = [], [], []
    previous = None
    with (folder/"measure.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            t = float(r["t_s"])
            valid = (r["valid"] == "True" and r["fault"] == "False"
                     and r["restore_failed"] == "False" and r["channel"] == "A")
            if start is None and valid and r["switching"] == "True":
                start = t
            gap = int(r["sequence_gap"]) != 0 or (previous is not None and not 0 < t-previous <= .01)
            previous = t
            if gap or not valid:
                batch = []
                raw.append([t]+[math.nan]*4)
                averaged.append([t]+[math.nan]*4)
            if not valid:
                continue
            v, i = float(r["vin_mv"])/1000, float(r["iin_ma"])/1000
            row = [t, v, i, v*i, float(r["duty"])/10]
            raw.append(row)
            batch.append(row)
            if len(batch) == 10:
                averaged.append(np.mean(batch,axis=0).tolist())
                batch = []
    if start is None:
        raise ValueError("No valid PWM startup found; raw data retained")
    raw, averaged = np.array(raw), np.array(averaged)
    for a in (raw, averaged):
        a[:,0] -= start
    raw = raw[(raw[:,0]>=-before)&(raw[:,0]<=after)]
    averaged = averaged[(averaged[:,0]>=-before)&(averaged[:,0]<=after)]
    fig, axes = plt.subplots(4,1,sharex=True,figsize=(11,10),layout="constrained")
    labels = ["Input voltage (V)","Input current (A)","Input power (W)","Duty (%)"]
    for n,(ax,label) in enumerate(zip(axes,labels),1):
        ax.plot(raw[:,0],raw[:,n],color="#9ba8b5",alpha=.5,lw=.4,label="Raw samples",rasterized=True)
        ax.plot(averaged[:,0],averaged[:,n],color="#168b87",lw=1,label="10-sample average")
        ax.axvline(0,color="gray",ls=":",lw=.8)
        ax.set(ylabel=label,xlim=(-before,after))
        ax.grid(alpha=.2)
        if n<=3:
            ax.axhline(float(peak[["vin_v","iin_a","pin_w"][n-1]]),color="#222222",ls="--",lw=1,
                       label="8 A IV-reference MPP")
        ax.legend(fontsize=8,loc="best")
    axes[0].set_ylim(bottom=0)
    axes[-1].set_xlabel("Time from first valid PWM-active sample (s, MCU clock)")
    fig.suptitle("MPPT startup transient - 8 A source limit" + (" - PARTIAL CAPTURE" if not run["complete"] else ""))
    fig.supxlabel("10-sample means are non-overlapping; gaps reset averaging. Dashed MPP is a separate-run reference.\nTime zero is telemetry-observed PWM startup, not a scope-triggered switching edge.",fontsize=8)
    for ext in ("svg","png"):
        fig.savefig(folder/f"startup_transient.{ext}",dpi=180)
    plt.close(fig)
    with (folder/"startup_transient_average_10samples.csv").open("w",newline="") as f:
        w=csv.writer(f);w.writerow(["time_from_pwm_s","vin_v","iin_a","pin_w","duty_pct"]);w.writerows(averaged)
    (folder/"startup_transient.json").write_text(json.dumps(dict(first_switching_mcu_s=start,
        before_s=before,after_s=after,complete=run["complete"],reference=str(reference),
        reference_mpp={k:float(peak[k]) for k in ["vin_v","iin_a","pin_w"]},
        data_quality=run.get("data_quality"),average_samples=10),indent=2))
    print(f"Saved: {folder/'startup_transient.svg'}")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port")
    mode.add_argument("--replay",type=Path)
    parser.add_argument("--resource")
    parser.add_argument("--reference",type=Path,default=ROOT/"captures/measure_iv_curves")
    parser.add_argument("--output",type=Path,default=ROOT/"captures/measure_startup")
    parser.add_argument("--before",type=float,default=.2)
    parser.add_argument("--after",type=float,default=1)
    args=parser.parse_args()
    try:
        if not all(math.isfinite(v) and v>0 for v in (args.before,args.after)):
            raise ValueError("Plot windows must be positive and finite")
        _,peaks=reference_rows(args.reference)
        if len([p for p in peaks if float(p["source_current_a"])==8])!=1:
            raise ValueError("Need one usable 8 A reference MPP")
        folder=args.replay
        if folder is None:
            with SPS5162X.open(args.resource) as supply:
                result=runner.capture(supply,args.port,[CurrentStep(0,8)],output_dir=args.output,
                                      voltage_limit_v=35,final_hold_s=15,make_plots=False)
            folder=Path(result["folder"])
            print(f"Complete: {result['complete']}; supply OFF confirmed: {result['output_off_confirmed']}")
            for key in ("error","uart_error","shutdown_error"):
                if key in result: print(f"{key}: {result[key]}")
        plot_startup(folder,args.reference,args.before,args.after)
        return int(not json.loads((folder/"run.json").read_text())["complete"])
    except Exception as error:
        print(f"Startup capture failed: {error}")
        return 1

if __name__=="__main__":
    raise SystemExit(main())
