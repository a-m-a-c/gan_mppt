"""Measurement 5 profiles and offline plots; capture uses the existing gated runner."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from current_profile import CurrentStep, ramp_steps
import measure_mppt_profile as runner
from sps5162x import SPS5162X

ROOT = Path(__file__).resolve().parents[1]

def profile(kind):
    if kind == "continuous":
        # 7.5 A / 600 s; 0.5 s commands give 6/7 mA quantized increments.
        return ramp_steps(.5, 8, 600, .5), 5
    if kind == "staircase":
        return [CurrentStep(i * 30, (i + 1) * .5) for i in range(16)], 30
    if kind == "timescale":
        return [CurrentStep(i * 10, i + 1) for i in range(8)], 10
    raise ValueError(kind)

def read(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def reference_rows(folder):
    curves = [r for r in read(folder / "settled_points.csv") if r["branch"] == "up"]
    peaks = sorted([r for r in read(folder / "reference_mpp.csv")
                    if r["branch"] == "up" and r["reference_usable"] == "True"],
                   key=lambda r: float(r["source_current_a"]))
    if not curves or not peaks:
        raise ValueError("Reference requires up-branch IV points and usable MPPs")
    return curves, peaks

def averaged_rows(folder, count):
    batch, output, previous = [], [], None
    with (folder / "measure.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            t = float(r["t_s"])
            valid = (r["valid"] == "True" and r["switching"] == "True"
                     and r["fault"] == "False" and r["restore_failed"] == "False" and r["channel"] == "A")
            gap = int(r["sequence_gap"]) != 0 or (previous is not None and not 0 < t-previous <= .01)
            previous = t
            if not valid or gap:
                batch = []
                if output and np.isfinite(output[-1][2]):
                    output.append([t, float(r["t_host_s"]), np.nan, np.nan, np.nan])
            if not valid:
                continue
            v, i = float(r["vin_mv"])/1000, float(r["iin_ma"])/1000
            batch.append([t, float(r["t_host_s"]), v, i, v*i])
            if len(batch) == count:
                output.append(np.mean(batch, axis=0).tolist())
                batch = []
    data = np.array(output)
    if data.size == 0 or not np.isfinite(data[:,2]).any():
        raise ValueError("No complete groups of active samples; raw capture retained")
    return data

def plot(folder, kind, reference, count, *, time_title=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    run = json.loads((folder / "run.json").read_text())
    data = averaged_rows(folder, count)
    out = folder / "comparison"
    out.mkdir(exist_ok=True)
    stem = "mppt_" + kind + "_average_" + str(count) + "samples"
    curves, peaks = reference_rows(reference)
    reference_steps = []
    if kind == "timescale":
        fig, axes = plt.subplots(4, 1, sharex=True, figsize=(11,10), layout="constrained")
        events = read(folder / "profile.csv")
        origin = float(events[0]["command_start_s"])
        times = [float(r["command_start_s"])-origin for r in events]
        currents = [float(r["requested_current_a"]) for r in events]
        end = run.get("profile", {}).get("output_off_s", float(data[-1,1]))-origin
        axes[0].step(times+[end], currents+[currents[-1]], where="post")
        axes[1].plot(data[:,1]-origin, data[:,2], lw=.7, label="Measured input voltage")
        axes[2].plot(data[:,1]-origin, data[:,3], lw=.7, label="Measured input current")
        axes[3].plot(data[:,1]-origin, data[:,4], lw=.7, label="Measured input power")
        for n, (start, current) in enumerate(zip(times, currents)):
            matches = [r for r in peaks if abs(float(r["source_current_a"])-current) < 1e-6]
            if len(matches) != 1:
                raise ValueError(f"Expected one usable IV reference MPP at {current:g} A")
            peak = matches[0]
            finish = times[n+1] if n+1 < len(times) else end
            reference_steps.append(dict(source_current_a=current, start_s=start, end_s=finish,
                                        vmpp_v=float(peak["vin_v"]), impp_a=float(peak["iin_a"]),
                                        pmpp_w=float(peak["pin_w"])))
            for ax, key in ((axes[1], "vin_v"), (axes[2], "iin_a"), (axes[3], "pin_w")):
                ax.hlines(float(peak[key]), start, finish, color="#222222", linestyle="--",
                          linewidth=1.2, label="IV-curve reference MPP" if n == 0 else None)
        axes[1].legend(fontsize=8)
        axes[2].legend(fontsize=8)
        axes[3].legend(fontsize=8)
        for ax, label in zip(axes, ["Source limit (A)", "Input voltage (V)", "Input current (A)", "Input power (W)"]):
            ax.set_ylabel(label)
            for t in times:
                ax.axvline(t, color="gray", ls=":", lw=.5)
        axes[-1].set_xlabel("Host time from first source command (s)")
        title = time_title or "MPPT response: 1-8 A, 10 seconds per step"
    else:
        fig, axes = plt.subplots(1,2,figsize=(14,7),layout="constrained")
        shown = data[np.isfinite(data[:,2])]
        for ax in axes:
            for n, sweep in enumerate(sorted(set(r["sweep_index"] for r in curves), key=int)):
                rows = [r for r in curves if r["sweep_index"] == sweep]
                ax.plot([float(r["iin_a"]) for r in rows], [float(r["vin_v"]) for r in rows],
                        color="#b4becb", lw=.8, alpha=.65, label="Reference IV curves" if n==0 else None)
            dots = ax.scatter(shown[:,3],shown[:,2],c=shown[:,0],cmap="viridis",s=3,alpha=.55,
                              rasterized=True,label=f"MPPT: {count}-sample means")
            ax.plot([float(r["iin_a"]) for r in peaks], [float(r["vin_v"]) for r in peaks],
                    "o--",color="#1f2937",ms=4,lw=1.2,label="Separate-run reference MPP")
            ax.set(xlabel="Converter input current (A)",ylabel="Converter input voltage (V)",xlim=(-.1,8.3))
        axes[0].set(title="Full VI trajectory")
        axes[1].set(title="MPP region",ylim=(20,28))
        axes[0].legend(fontsize=8)
        fig.colorbar(dots,ax=axes,label="MCU capture time (s)",shrink=.8)
        title = "MPPT: " + ("0.5-8 A over 600 s + 5 s final hold" if kind=="continuous" else "0.5-8 A in 0.5 A steps, 30 s holds")
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle(title + (" - PARTIAL CAPTURE" if not run["complete"] else ""))
    fig.supxlabel(f"Non-overlapping {count}-sample means; groups reset at gaps. Raw data retained."
                  + (" Separate-run IV reference: source/temperature drift applies." if kind!="timescale"
                     else "\nDashed: separate-run IV reference MPP (source/temperature drift applies). Host timing is approximate."),fontsize=8)
    for ext in ("svg","png"):
        fig.savefig(out / f"{stem}.{ext}",dpi=180)
    plt.close(fig)
    with (out / f"{stem}.csv").open("w",newline="") as f:
        w=csv.writer(f); w.writerow(["mean_mcu_s","mean_host_s","mean_vin_v","mean_iin_a","mean_pin_w"]); w.writerows(data)
    (out / f"{stem}.json").write_text(json.dumps(dict(kind=kind,average_samples=count,
        complete=run["complete"],data_quality=run.get("data_quality"),reference=str(reference),reference_steps=reference_steps,
        averaging="Non-overlapping active samples; reset at invalid/sequence/time gaps >10 ms; incomplete groups discarded"),indent=2))
    print(f"Saved: {out / (stem + '.svg')}")

def main(kind):
    parser=argparse.ArgumentParser(description=f"Measurement 5 {kind}; capture enables supply and channel A MPPT.")
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port")
    mode.add_argument("--replay",type=Path)
    parser.add_argument("--resource")
    parser.add_argument("--reference",type=Path,default=ROOT/"captures/measure_iv_curves")
    parser.add_argument("--output",type=Path,default=ROOT/"captures/measure_5_redo"/kind)
    parser.add_argument("--average-samples",type=int,default=10)
    args=parser.parse_args()
    try:
        if args.average_samples<1:
            raise ValueError("Average sample count must be positive")
        reference_rows(args.reference)  # Validate before enabling hardware.
        folder=args.replay
        if folder is None:
            steps,hold=profile(kind)
            with SPS5162X.open(args.resource) as supply:
                result=runner.capture(supply,args.port,steps,output_dir=args.output,
                                      final_hold_s=hold,make_plots=False)
            folder=Path(result["folder"])
            print(f"Complete: {result['complete']}; supply OFF confirmed: {result['output_off_confirmed']}")
            for key in ("error","uart_error","shutdown_error"):
                if key in result: print(f"{key}: {result[key]}")
        plot(folder,kind,args.reference,args.average_samples)
        return int(not json.loads((folder/"run.json").read_text())["complete"])
    except Exception as error:
        print(f"Measurement failed: {error}")
        return 1
