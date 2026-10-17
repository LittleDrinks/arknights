#!/usr/bin/env python3
"""Local read-only source extraction and result checks. Does not invoke any simulator."""
import csv, hashlib, json, pathlib, subprocess
ROOT=pathlib.Path(__file__).resolve().parent
WORK=ROOT.parent
GAME=WORK.parent/'Stronghold-Protocol'
CORPUS=WORK.parent/'knowledge/corpus'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,obj): (ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
ids=['BV1xVdBBWEuu','BV1X3XUB9EQb','BV1g9AGz1EbT','BV1bAd4BhEJa','BV16YSUBZETc','BV1kgwjzqExY','BV1agw6zREss']
extra={'BV1xVdBBWEuu':[(590,823),(970,1085)],'BV16YSUBZETc':[(149,302)],
       'BV1kgwjzqExY':[(700,865)],'BV1g9AGz1EbT':[(410,530)]}
guides=[];sources=[]
for bid in ids:
    cp=CORPUS/'distilled'/f'{bid}.json';rp=CORPUS/'raw'/f'subtitle_{bid}.json'
    card=json.loads(cp.read_text());raw=json.loads(rp.read_text());lines=rp.read_text().splitlines()
    index_lines={int(s.split(':')[1].rstrip(', ')):i+1 for i,s in enumerate(lines) if '"index":' in s}
    windows=[(r['evidence']['start_seconds'],r['evidence']['end_seconds']) for r in card['rules']]+extra.get(bid,[])
    guides.append({'card':card,'cardSha256':sha(cp),'rawSha256':sha(rp),'windows':[
        {'start':a,'end':b,'segments':[dict(s,sourceLine=index_lines[s['index']]) for s in raw
           if float(s['from'].rstrip('s'))<=b and float(s['to'].rstrip('s'))>=a]} for a,b in windows]})
    sources.extend([cp,rp])
write('guides.json',guides)
data={n:json.loads((GAME/'data'/f'{n}.json').read_text()) for n in ['chess','items','bonds','garrisons','bosses','bands']}
ins=[json.loads(p.read_text()) for p in sorted((ROOT/'results').glob('*.input.json'))]
chids={u['chessId'] for d in ins for u in d['roster'] if 'chessId' in u}
itids={i for d in ins for u in d['roster'] for i in u.get('items',[])}
gids={g for cid in chids for g in data['chess'][cid]['garrisonIds']}
bids=['yanShip','kjeragShip','kazimierzShip','sargonShip','siracusaShip','skillfulShip','arcaneShip','preciShip','swiftShip','raidShip','suntShip','maniShip']
write('mechanics.json',{'sourceNote':'Minified data/*.json line 1; object keys below are exact JSON pointers.',
  'chess':{k:{f:data['chess'][k].get(f) for f in ['name','isGolden','tier','bonds','backup','garrisonIds','stats']} for k in sorted(chids)},
  'items':{k:data['items'][k] for k in sorted(itids)},'bonds':{k:data['bonds'][k] for k in bids},
  'garrisons':{k:data['garrisons'][k] for k in sorted(gids)},
  'bossPoolsAbyss':{k:v['bloodPoint']['ABYSS'] for k,v in data['bosses'].items()},'band':data['bands']['band_bldsk']})
replay=GAME/'public/dev/recordings/agent-solo-ABYSS-s1-r14-13.json'
r=json.loads(replay.read_text());sources.append(replay)
exp=WORK.parent/'EXPERIMENTS.md';sources.append(exp)
natural={'level':'EXISTING_NATURAL_ARTIFACT_NOT_RERUN','replayPath':str(replay.relative_to(WORK.parent)),
  'sha256':sha(replay),'provenanceLimit':'Local untracked replay has no embedded engine commit; not an independently rerun natural match and not proof of every model roster.',
  'round':r['round'],'modeId':r['modeId'],'duration':r['duration'],
  'roster':[dict(u,itemNames=[data['items'][i]['name'] for i in u.get('items',[])]) for u in r['field']['units'] if u['kind']=='op'],
  'result':r['result'],'initialBossPool':r['frames'][0]['snap']['boss'],
  'experimentLedgerLines':[{ 'line':i+1,'text':s} for i,s in enumerate(exp.read_text().splitlines()) if '1376' in s or '4288' in s],
  'countLimit':'1376 baseline and 4288 PPO totals are checked against the existing experiment ledger, not reaggregated or rerun here.'}
write('natural-evidence.json',natural)
rows=[]
terrain_probe=json.loads((ROOT/'results/yan-200-attribution-full.jsonl').read_text())
unowned=[h for h in terrain_probe['metrics']['uncreditedBossHits'] if h['source'] is None and h['credit'] is None]
assert all(h['dmg']['tags']==['terrain'] for h in unowned)
assert abs(sum(h['amount'] for h in unowned)-terrain_probe['metrics']['unownedPoolDamage'])<1e-5
for p in sorted((ROOT/'results').glob('*.jsonl')):
    if not p.stat().st_size:continue
    d=json.loads(p.read_text());s=json.loads(p.with_suffix('.input.json').read_text())
    assert d['naturalFullRun'] is False and d['goalCompleted'] is False
    assert d['bossPoolInitial']==3_000_000 and d['result']['errors']==0 and not d['result'].get('synthetic')
    ops=[u for u in s['roster'] if u['kind']=='chess']
    assert len(ops)==8 and all(len(u['items'])<=2 for u in ops)
    assert all(data['chess'][u['chessId']]['isGolden'] for u in ops)
    assert all(0<=b['layers']<=999 for b in s['bonds'].values())
    pool_unattributed=d['bossPoolInitial']-d['bossPoolLeft']-sum(pp['bossDamage'] for pp in d['result']['perPlayer'].values())
    if d['build']=='yan' and d['layer']==200:
        assert d['result']==terrain_probe['result'], 'observer-only reruns must preserve the exact battle result'
        assert abs(pool_unattributed-terrain_probe['metrics']['unownedPoolDamage'])<1e-5
    else: assert abs(pool_unattributed)<1e-5
    assert all(not pp['layerGains'] for pp in d['result']['perPlayer'].values())
    rows.append({k:d[k] for k in ['build','layer','variant','seed','time','r14BossKilled','bossPoolLeft','teamLp','overtimeLp']}|
                {'hitCapCount':d['metrics']['hitCapCount'],'maxCancelledHit':d['metrics']['maxCancelledHit'],
                 'poolUnattributed':round(pool_unattributed,5)})
with (ROOT/'summary.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
def positions(case):
    s=json.loads((ROOT/'results'/f'{case}.input.json').read_text())
    return {u['uid']:(u['chessId'],u['row'],u['col'],u['dir']) for u in s['roster'] if u['kind']=='chess'}
for base,variants in [('kjerag-500-bot',['kjerag-500-core-only']),
                      ('sargon-200-bot',['sargon-200-core-only','sargon-200-no-compass','sargon-200-no-items'])]:
    assert all(positions(base)==positions(v) for v in variants)
a,b=positions('kjerag-500-bot'),positions('kjerag-500-adjacent')
assert [a[k][0] for k in a if a[k]!=b[k]]==['chess_char_6_02_b']
pin='6ab2468279e926d107788f63d7fe219b85f67b4a'
assert subprocess.check_output(['git','-C',str(GAME),'rev-parse','HEAD'],text=True).strip()==pin
subprocess.run(['git','-C',str(GAME),'diff','--exit-code',pin,'--','server','shared','data','test/match/harness.js'],check=True,stdout=subprocess.DEVNULL)
tracked=subprocess.check_output(['git','-C',str(GAME),'ls-files','server','shared','data','test/match/harness.js'],text=True).splitlines()
sources.extend(GAME/p for p in tracked)
sources.extend(WORK/p for p in ['rl/controller.mjs','controller-v2/control.mjs','rl/env.mjs','rl/features.mjs'])
write('sources.json',{'enginePin':pin,'rulesDiffFromPin':'empty',
  'gameDirtyOutsideRules':subprocess.check_output(['git','-C',str(GAME),'status','--short'],text=True).splitlines(),
  'sourceHashes':{str(p.relative_to(WORK.parent)):sha(p) for p in sorted(set(sources))},
  'artifactHashes':{str(p.relative_to(ROOT)):sha(p) for p in sorted(ROOT.rglob('*')) if p.is_file() and p.name!='sources.json'}})
budget=json.loads((ROOT/'results/budget.json').read_text());size=sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file())
assert budget['usedSeconds']<40 and size<20_000_000
assert all(r['returncode']==0 and r['stopReason'] is None and r['peakSampledCombinedRssBytes']<768*1024**2 for r in budget['runs'])
assert len(rows)==len(budget['runs'])==20
print(json.dumps({'verifiedConstructedBattles':len(rows),'simWorkerWallSeconds':budget['usedSeconds'],
 'peakSampledCombinedMiB':max(x['peakSampledCombinedRssBytes'] for x in budget['runs'])/1024**2,
 'artifactBytes':size,'newNaturalGames':0,'status':'PASS'},ensure_ascii=False))
