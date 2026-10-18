#!/usr/bin/env python3
"""Resolve an old receipt path without modifying the immutable receipt itself."""
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('path');a=p.parse_args()
value=a.path
for prefix in (str(ROOT),'/data1/zsm/arknights','/home/tzb/zsm/arknights'):
    if value.startswith(prefix+'/'):value=value[len(prefix)+1:];break
mapping=json.loads((ROOT/'results/migrations/layout-20261018/prefix-map.json').read_text())
for old,new in sorted(mapping.items(),key=lambda x:len(x[0]),reverse=True):
    if value==old or value.startswith(old+'/'):
        print(ROOT/(new+value[len(old):]));break
else:raise SystemExit('No legacy mapping for '+a.path)
