"""Offline ramp/reference overlay. No hardware access."""
import csv
import json
import math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

folder=Path(__file__).resolve().parent
captures=folder.parents[1]
out=folder/'comparison'
out.mkdir(exist_ok=True)
def read(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))
run=json.loads((folder/'run.json').read_text())
assert run['complete']
with (folder/'measure.csv').open() as f:
    data=np.array([(float(r['t_s']),float(r['t_host_s']),float(r['vin_mv'])/1000,
                   float(r['iin_ma'])/1000) for r in csv.DictReader(f)
                  if r['valid']=='True' and r['switching']=='True' and r['fault']=='False' and r['channel']=='A'])
points=read(captures/'measure_23_new/settled_points.csv')
peaks=sorted([r for r in read(captures/'measure_23_new/reference_mpp.csv')
              if r['reference_usable']=='True' and r['branch']=='up'],key=lambda r:float(r['source_current_a']))
stair=read(captures/'measure_5/comparison/mppt_pi_fixed_overlay.csv')
stride=math.ceil(len(data)/25000)
shown=data[::stride]
fig,axes=plt.subplots(1,2,figsize=(14,7),layout='constrained')
for ax in axes:
    for n in range(1,17):
        ps=[r for r in points if int(r['sweep_index'])==n and r['branch']=='up']
        ax.plot([float(r['iin_a']) for r in ps],[float(r['vin_v']) for r in ps],
                color='#b4becb',alpha=.65,lw=.8,label='Reference IV curves' if n==1 else None)
    dots=ax.scatter(shown[:,3],shown[:,2],c=shown[:,0],cmap='viridis',s=3,alpha=.55,
                    vmin=0,vmax=310,rasterized=True,label='Latest 5-minute ramp')
    ax.plot([float(r['iin_a']) for r in peaks],[float(r['vin_v']) for r in peaks],
            'o--',color='#1f2937',lw=1.4,ms=4,label='Reference MPP path',zorder=4)
    ax.scatter([float(r['iin_a']) for r in stair],[float(r['vin_v']) for r in stair],
               marker='D',s=37,color='#e18335',edgecolor='white',lw=.6,zorder=5,
               label='Accepted staircase: final 5 s means')
    ax.set(xlabel='Converter input current (A)',ylabel='Converter input voltage (V)',xlim=(-.1,8.3))
    ax.grid(alpha=.2)
axes[0].set(title='Full VI trajectory',ylim=(12,30))
axes[1].set(title='MPP region',ylim=(20,28))
axes[0].legend(loc='lower left',fontsize=8,framealpha=.95)
fig.colorbar(dots,ax=axes,label='MCU capture time (s)',shrink=.8,pad=.02)
fig.suptitle('Five-minute MPPT ramp vs measured reference\n0.5-8 A over 300 s + 5 s final hold | 31 October 2026, 11:14 run',fontsize=14)
fig.supxlabel('Ramp dots are display-subsampled; staircase diamonds are settled means from the accepted 30-second holds.\nSeparate-run reference: temperature/source drift can shift MPP. Capture complete; 3 missing measurement records, 2 CRC errors.',fontsize=9)
for ext in ('png','svg'):
    fig.savefig(out/f'mppt_ramp_overlay.{ext}',dpi=180)
plt.close(fig)
end=run['profile']['profile_start_s']+305
last=data[(data[:,1]>=end-4.9)&(data[:,1]<end-.1)]
summary=dict(source=str(folder.relative_to(captures)),reference='measure_23_new',
             staircase='measure_5',complete=run['complete'],data_quality=run['data_quality'],
             active_samples=len(data),display_stride=stride,
             final_hold=dict(window_seconds=4.8,vin_v=float(last[:,2].mean()),
                             iin_a=float(last[:,3].mean()),pin_w=float((last[:,2]*last[:,3]).mean())))
(out/'mppt_ramp_overlay.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print(json.dumps(summary,indent=2))
