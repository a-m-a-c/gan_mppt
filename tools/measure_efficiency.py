"""Capture or replay conversion efficiency; capture requires firmware flashed at the labelled frequency."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from measure_5_common import ROOT, profile, read
import measure_mppt_profile as runner
from sps5162x import SPS5162X


def plot(folder, kind, frequency):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    run=json.loads((folder/"run.json").read_text())
    origin=run["profile"]["profile_start_s"]
    if kind=="continuous":
        windows=[(origin+t,origin+t+5) for t in range(5,600,5)]
    else:
        events=read(folder/"profile.csv")
        ends=[float(r["command_start_s"]) for r in events[1:]]+[origin+480]
        windows=[(end-6,end-1) for end in ends]
    totals=np.zeros((len(windows),3))
    index=0
    with (folder/"measure.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            t=float(r["t_host_s"])
            while index<len(windows) and t>=windows[index][1]: index+=1
            if index==len(windows): break
            if t<windows[index][0]: continue
            if not (r["valid"]=="True" and r["switching"]=="True" and r["fault"]=="False"
                    and r["restore_failed"]=="False" and r["channel"]=="A"):continue
            v,i,vo,io=[float(r[k])/1000 for k in ("vin_mv","iin_ma","vout_mv","iout_ma")]
            totals[index]+=[1,v*i,vo*io]
    rows=[]
    for (start,end),(n,pi,po) in zip(windows,totals):
        if n and pi>0: rows.append([start,end,pi/n,po/n,100*po/pi,int(n)])
    if not rows: raise ValueError("No valid efficiency windows; raw data retained")
    out=folder/"comparison";out.mkdir(exist_ok=True)
    with (out/"conversion_efficiency.csv").open("w",newline="") as f:
        w=csv.writer(f);w.writerow(["start_host_s","end_host_s","mean_pin_w","mean_pout_w","efficiency_pct","samples"]);w.writerows(rows)
    fig,ax=plt.subplots(figsize=(9,5),layout="constrained")
    ax.plot([r[2] for r in rows],[r[4] for r in rows],"o-",color="#168b87",ms=3,lw=1.4)
    ax.set(xlabel="Input power (W)",ylabel="Measured conversion efficiency (%)",
           title=f"Conversion efficiency - {frequency:g} kHz - {kind}"+(" - PARTIAL" if not run["complete"] else ""),
           xlim=(0,210))
    ax.set_ylim(min(80,min(r[4] for r in rows)-1),max(100,max(r[4] for r in rows)+.2))
    ax.grid(alpha=.25)
    method=("5 s bins; first 5 s and final hold excluded." if kind=="continuous" else
            "5 s windows ending 1 s before each step.")
    fig.supxlabel("Ratio of mean output to mean input power. "+method+"\nFrequency is a supplied firmware label, not telemetry readback. Sensor calibration uncertainty not established.",fontsize=8)
    for ext in ("svg","png"):fig.savefig(out/f"conversion_efficiency.{ext}",dpi=180)
    plt.close(fig)
    peak=max(rows,key=lambda r:r[4])
    (out/"conversion_efficiency.json").write_text(json.dumps(dict(kind=kind,frequency_khz=frequency,
        frequency_provenance="User-supplied flashed firmware label; not read back",method=method,
        data_quality=run.get("data_quality"),complete=run["complete"],peak_efficiency_pct=peak[4],peak_input_w=peak[2]),indent=2))
    print(f"Saved: {out/'conversion_efficiency.svg'}; peak {peak[4]:.3f}% at {peak[2]:.2f} W")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    m=p.add_mutually_exclusive_group(required=True);m.add_argument("--port");m.add_argument("--replay",type=Path)
    p.add_argument("--kind",choices=["continuous","staircase"],required=True)
    p.add_argument("--frequency-khz",type=int,choices=[300,600],default=300)
    p.add_argument("--resource")
    p.add_argument("--output",type=Path,default=ROOT/"captures/measure_effiency")
    a=p.parse_args()
    try:
        folder=a.replay
        if folder is None:
            steps,hold=profile(a.kind)
            with SPS5162X.open(a.resource) as supply:
                result=runner.capture(supply,a.port,steps,output_dir=a.output/f"{a.frequency_khz}kHz"/a.kind,
                                      final_hold_s=hold,make_plots=False)
            folder=Path(result["folder"])
            (folder/"efficiency_capture.json").write_text(json.dumps(dict(kind=a.kind,frequency_khz=a.frequency_khz,
                frequency_provenance="Expected flashed firmware; not read back"),indent=2))
            print(f"Complete: {result['complete']}; supply OFF confirmed: {result['output_off_confirmed']}")
        plot(folder,a.kind,a.frequency_khz)
        return int(not json.loads((folder/"run.json").read_text())["complete"])
    except Exception as error:
        print(f"Efficiency capture failed: {error}");return 1

if __name__=="__main__":raise SystemExit(main())
