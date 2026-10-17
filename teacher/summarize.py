#!/usr/bin/env python3
"""Audit recorded natural runs; does not run or alter the game."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results'
screen = json.loads((OUT / 'screen.json').read_text())
rows = []
for case in screen['cases']:
    if case['status'] != 'complete':
        continue
    name = case['variant'] + '-' + case['budget']
    matches = json.loads((OUT / (name + '-trace.json')).read_text())
    row = {k: case[k] for k in ['variant', 'budget', 'games', 'wins', 'errors', 'rounds']}
    row.update(macroActions={'refresh': 0, 'acquire': 0, 'lappland': 0}, macroSeeds=[],
               coreBuys=len([b for m in matches for b in m['buys']]), threeBondsActive=[])
    for match in matches:
        has_macro = False
        for prep in match['preps']:
            if all(b.get('active') for b in prep['before']['bonds'].values()):
                row['threeBondsActive'].append([match['seed'], prep['round']])
            s = prep['teacher']
            if not s:
                continue
            cfg = match['config']
            assert s['setupCost'] <= cfg['setupCost']
            assert s['setupBuys'] <= cfg['setupBuys']
            assert s['attempted'] <= cfg['maxActions']
            assert s['purchases'] <= cfg['maxPurchases']
            assert s['cost'] <= cfg['maxNetCost']
            assert not s['failed'], (name, match['seed'], prep['round'])
            for report in s['macros']:
                assert report['netCost'] == report['before']['funds'] - report['after']['funds']
                for a in report['actions']:
                    if a['accepted']:
                        has_macro = True
                        row['macroActions'][a['module']] += 1
        if has_macro:
            row['macroSeeds'].append(match['seed'])
    rows.append(row)

mixed = json.loads((OUT / 'mixed-aggressive-trace.json').read_text())
match = next(m for m in mixed if m['seed'] == 3)
p9 = next(p for p in match['preps'] if p['round'] == 9)
p10 = next(p for p in match['preps'] if p['round'] == 10)
aroma = next(b for b in match['buys'] if b['round'] == 9 and b['id'] == 'chess_char_4_10_a')
assert any(p['uid'] == aroma['uid'] and p['id'] == aroma['id'] for p in p9['before']['board'])
assert any(b['id'] == aroma['id'] and b['result'].get('ok') for b in p9['teacher']['buys'])
assert p9['after']['bonds']['arcaneShip']['layers'] - p9['before']['bonds']['arcaneShip']['layers'] == 6
assert all(b['active'] and b['tier'] >= 1 for b in p10['before']['bonds'].values())
assert p10['teacher']['macros'][0]['accepted'] == 3
assert p10['teacher']['macros'][0]['netCost'] == 3
assert p10['after']['bonds']['siracusaShip']['layers'] - p10['before']['bonds']['siracusaShip']['layers'] == 6
assert p10['after']['bonds']['arcaneShip']['layers'] - p10['before']['bonds']['arcaneShip']['layers'] == 6
acquire_match = next(m for m in mixed if m['seed'] == 4)
xiaoman = next(b for b in acquire_match['buys'] if b['round'] == 4 and b['id'] == 'chess_char_2_04_a')
p6 = next(p for p in acquire_match['preps'] if p['round'] == 6)
assert any(p['uid'] == xiaoman['uid'] for p in p6['before']['board'])
assert p6['before']['bonds']['yanShip']['active']
assert any(a['module'] == 'acquire' and a['action'] == 'buy' and a['accepted']
           for r in p6['teacher']['macros'] for a in r['actions'])
assert p6['after']['bonds']['yanShip']['layers'] - p6['before']['bonds']['yanShip']['layers'] == 3

evidence = {'kind': 'natural botbench evidence; no constructed game state',
    'engineCommit': '6ab2468279e926d107788f63d7fe219b85f67b4a',
    'sourceHashes': {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [HERE / 'loader.mjs', HERE / 'policy.mjs', HERE / 'observe.mjs']},
    'elapsedSeconds': screen['elapsedSeconds'],
    'peakChildRSSBytes': max(c['peakRSSBytes'] for c in screen['cases']),
    'budgetAudit': 'passed for all captured teacher preps', 'cases': rows,
    'witness': {'file': 'mixed-aggressive-trace.json', 'seed': 3, 'purchase': aroma,
                'round9': p9, 'round10': p10},
    'acquireWitness': {'file': 'mixed-aggressive-trace.json', 'seed': 4, 'purchase': xiaoman, 'round6': p6},
    'limits': ['Same four seeds reused across variants; not 28 independent seeds.',
               'No ABYSS wins. No claim of improvement over the reported prior 896 losses.',
               'No Lappland cycling macro actions in this screen; ordinary Lappland refresh traits did fire.',
               'Core purchases can consume a spare Lappland offer before the cycling macro sees it.',
               'Prep-end total gains include native bot activity; projected macro gain is not a counterfactual measurement.']}
(OUT / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
for r in rows:
    print(f"{r['variant']}/{r['budget']}: {r['wins']}/{r['games']} wins, mean rounds {sum(r['rounds'])/len(r['rounds']):.2f}, "
          f"macro actions {r['macroActions']}, seeds {r['macroSeeds']}, core buys {r['coreBuys']}")
print('Natural purchase/deployment/activation/layer witness and per-prep budget audit: PASS')
