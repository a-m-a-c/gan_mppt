"""Offline plot; run from the repository root. Never accesses hardware."""
import csv
import json
import math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
LATEST = ROOT / 'captures/measure_5'
REFERENCE = ROOT / 'captures/measure_23_new'

def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def active(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return np.array([(float(r['t_host_s']), float(r['vin_mv'])/1000,
                          float(r['iin_ma'])/1000) for r in csv.DictReader(f)
                         if r['valid']=='True' and r['switching']=='True'
                         and r['fault']=='False' and r['channel']=='A'])

run = json.loads((LATEST/'run.json').read_text())
data = active(LATEST/'measure.csv')
old = active(ROOT/'captures/measure_5/iterations/01_original_50mv/measure.csv')
points = rows(REFERENCE/'settled_points.csv')
peaks = sorted([r for r in rows(REFERENCE/'reference_mpp.csv')
                if r['reference_usable']=='True' and r['branch']=='up'],
               key=lambda r: float(r['source_current_a']))
profile = rows(LATEST/'profile.csv')
previous = rows(OUT/'mppt_settled_overlay.csv')
assert run['complete'] and run['measurement']['mppt_records'] > 0
stats = []
for n, level in enumerate(profile):
    end = (float(profile[n+1]['command_start_s']) if n+1<len(profile)
           else run['profile']['profile_start_s'] + run['profile']['steps'][-1]['time_s']
           + run['profile']['final_hold_s'])
    # End 100 ms early to exclude command/transport-boundary ambiguity.
    start, stop = end-5.1, end-.1
    selected = data[(data[:,0]>=start)&(data[:,0]<stop)]
    assert len(selected)>5000, (n, len(selected))
    current = float(level['requested_current_a'])
    ref = next(r for r in peaks if float(r['source_current_a'])==current)
    stats.append(dict(source_current_a=current, samples=len(selected),
                      start_host_s=start, end_host_s=stop,
                      vin_v=float(selected[:,1].mean()), iin_a=float(selected[:,2].mean()),
                      pin_w=float((selected[:,1]*selected[:,2]).mean()),
                      reference_vin_v=float(ref['vin_v']), reference_pin_w=float(ref['pin_w'])))

fig, axes = plt.subplots(1,2,figsize=(14,7),layout='constrained')
for ax in axes:
    for index in range(1,17):
        ps = [r for r in points if int(r['sweep_index'])==index and r['branch']=='up']
        ax.plot([float(r['iin_a']) for r in ps], [float(r['vin_v']) for r in ps],
                color='#b2bac5',lw=.8,alpha=.7,label='Reference IV curves' if index==1 else None)
    ax.scatter(old[::25,2],old[::25,1],s=2,color='#7e9ab5',alpha=.16,
               label='Original measure_5: 50 mV',rasterized=True)
    stride=math.ceil(len(data)/25000)
    ax.scatter(data[::stride,2],data[::stride,1],s=2,color='#168968',alpha=.3,
               label='New run: fractional PI + settled averaging',rasterized=True)
    ax.plot([float(r['iin_a']) for r in previous],[float(r['vin_v']) for r in previous],
            's:',color='#c97737',lw=1,ms=4,label='Before PI fix: final 5 s means',zorder=4)
    ax.plot([float(r['iin_a']) for r in peaks],[float(r['vin_v']) for r in peaks],
            'o--',color='#202735',lw=1.3,ms=4,label='Reference MPP path',zorder=4)
    ax.scatter([r['iin_a'] for r in stats],[r['vin_v'] for r in stats],
               marker='D',s=40,color='#00a87a',edgecolor='white',lw=.8,
               label='New run: final 5 s mean per level',zorder=5)
    ax.set(xlabel='Converter input current (A)',ylabel='Converter input voltage (V)',xlim=(-.1,8.3))
    ax.grid(alpha=.2)
axes[0].set(title='Full VI trajectory',ylim=(12,30))
axes[1].set(title='MPP region',ylim=(20,28))
axes[0].legend(loc='lower left',fontsize=8,framealpha=.95)
for r in stats:
    if r['source_current_a'] in (.5,1,2,4,6,8):
        axes[1].annotate(f"{r['source_current_a']:g} A",(r['iin_a'],r['vin_v']),
                         xytext=(5,-15),textcoords='offset points',fontsize=8,color='#00664a')
fig.suptitle('Fractional-PI MPPT vs measure_23_new reference\n0.5-8 A, 0.5 A steps, 30 s holds | 30 October 2026, 22:09 run',fontsize=14)
fig.supxlabel('Dots are display-subsampled; means use all valid samples. Labels are source current settings.\nReference sweeps were measured separately; temperature/source drift can shift the MPP. UART: 220 missing measurement records, 33 CRC errors; 2 missing diagnostics.',fontsize=9)
for ext in ('png','svg'):
    fig.savefig(OUT/f'mppt_pi_fixed_overlay.{ext}',dpi=180)
plt.close(fig)
with (OUT/'mppt_pi_fixed_overlay.csv').open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(stats[0])); w.writeheader(); w.writerows(stats)
(OUT/'mppt_pi_fixed_overlay.json').write_text(json.dumps(dict(
    source=str(LATEST.relative_to(ROOT)), reference=str(REFERENCE.relative_to(ROOT)),
    baseline='captures/measure_5/iterations/01_original_50mv', before_pi_fix='captures/measure_5/iterations/02_settled_100mv_before_pi_fix', complete=run['complete'],
    data_quality=run['data_quality'], displayed_stride=stride,
    means='All valid switching samples in final 5 seconds, ending 100 ms before next command or scheduled stop; paired V*I.',
    plateaus=stats),indent=2)+'\n',encoding='utf-8')
print(json.dumps([r for r in stats if r['source_current_a'] in (0.5,1,2.5,4,8)],indent=2))
