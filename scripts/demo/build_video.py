import json, os, subprocess, sys
d=json.load(open('frames/frames.json'))
fr=d['frames']; mk=d['marks']
stop=[m for m in mk if m['name']=='stop'][0]['t']
lines=[]
for i,f in enumerate(fr):
    dur=(fr[i+1]['t']-f['t']) if i+1<len(fr) else max(0.1, stop-f['t'])
    lines.append(f"file '{os.path.abspath(f['file'])}'\nduration {max(dur,0.001):.4f}")
lines.append(f"file '{os.path.abspath(fr[-1]['file'])}'")
open('frames/list.txt','w').write('\n'.join(lines)+'\n')
t0=mk[0]['t']
json.dump({m['name']: round(m['t']-t0,2) for m in mk}, open('marks.json','w'))
print({m['name']: round(m['t']-t0,2) for m in mk})
