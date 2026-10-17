#!/usr/bin/env python3
"""One persistent, aggregate <=40 s local worker budget. Standard library only.
All worker imports, seed selection, setup and simulation count. No budget reset on retry.
"""
import json, os, pathlib, resource, signal, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parent
OUT=ROOT/'results'; OUT.mkdir(exist_ok=True)
LEDGER=OUT/'budget.json'
ledger=json.loads(LEDGER.read_text()) if LEDGER.exists() else {'limitSeconds':40,'stopAtSeconds':38,'limitBytes':20_000_000,'limitRssBytes':768*1024**2,'usedSeconds':0,'runs':[]}
cases=[tuple(a.split(':')) for a in sys.argv[1:]] or [(b,str(n),'bot') for n in [200,500,999] for b in ['kjerag','kazimierz','sargon','yan']]
cases=[(*c,'bot') if len(c)==2 else c for c in cases]
assert all(len(c)==3 for c in cases)
ledger['requestedCases']=sorted(set(ledger.get('requestedCases',[]))|{'-'.join(c) for c in cases})
def save():
    attempted={r['case'] for r in ledger['runs']}
    ledger['notRun']=[k for k in ledger['requestedCases'] if k not in attempted]
    LEDGER.write_text(json.dumps(ledger,indent=2)+'\n')
save()
def size(): return sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file())
def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE,(2_000_000,2_000_000))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
def rss(pid):
    try:
        return int(next(s.split()[1] for s in pathlib.Path(f'/proc/{pid}/status').read_text().splitlines() if s.startswith('VmRSS:')))*1024
    except (OSError,StopIteration): return 0
for case in cases:
    key='-'.join(case)
    if any(r['case']==key for r in ledger['runs']):
        print('already attempted:',key);continue
    remaining=ledger['stopAtSeconds']-ledger['usedSeconds']
    if remaining<=0 or size()>=19_000_000:
        print('NOT_RUN budget exhausted:',key);continue
    start=time.monotonic(); peak=0; stopped=None
    with (OUT/(key+'.jsonl')).open('wb') as out,(OUT/(key+'.stderr')).open('wb') as err:
        p=subprocess.Popen(['node','--max-old-space-size=480',str(ROOT/'fixture.mjs'),*case],stdout=out,stderr=err,
                           start_new_session=True,preexec_fn=limits,cwd=ROOT.parent)
        while p.poll() is None:
            peak=max(peak,rss(p.pid)+rss(os.getpid()))
            if time.monotonic()-start>=remaining: stopped='aggregate_wall_budget'
            elif peak>=740*1024**2: stopped='memory_budget'
            elif size()>=19_000_000: stopped='artifact_budget'
            if stopped: os.killpg(p.pid,signal.SIGKILL);break
            time.sleep(.01)
        p.wait()
    elapsed=time.monotonic()-start;ledger['usedSeconds']+=elapsed
    row={'case':key,'seconds':elapsed,'returncode':p.returncode,'stopReason':stopped,'peakSampledCombinedRssBytes':peak}
    ledger['runs'].append(row); ledger['artifactBytes']=size()
    save();print(json.dumps(row),flush=True)
    if stopped: break
    if p.returncode: print((OUT/(key+'.stderr')).read_text()[-3000:]);sys.exit(1)
