#!/usr/bin/env python3
"""Fetch only the upstream-listed sprites used by the recorded example, without changing manifests."""
import concurrent.futures
import json
import pathlib
import sys
import urllib.request

root = pathlib.Path(sys.argv[1]).resolve()
manifest = json.loads((root / 'data/assets.json').read_text())
sources = json.loads((root / 'docs/research/07-assets.json').read_text())
used = set()
pattern = sys.argv[2] if len(sys.argv) > 2 else 'agent-solo-ABYSS-s1-*.json'
for f in (root / 'public/dev/recordings').glob(pattern):
    rec = json.loads(f.read_text())
    units = list(rec['field']['units'])
    units += [e[1] for frame in rec['frames'] for e in frame['ev'] if e[0] == 'spawn']
    used.update(u.get('spine') for u in units if u.get('spine'))

files = {}
def add(local, source):
    url = source.get('url') if isinstance(source, dict) else source
    if local and url and url.startswith('https://raw.githubusercontent.com/'):
        files[local.removeprefix('/')] = url

for group, source_group in [('chars', 'operators'), ('enemies', 'enemies'), ('tokens', 'tokens')]:
    for key in used & manifest.get(group, {}).keys() & sources.get(source_group, {}).keys():
        m, s = manifest[group][key], sources[source_group][key]
        for local, source in [('avatar', s.get('avatar', {}).get('e0e1')), ('portrait', s.get('portrait', {}).get('e0e1')), ('icon', s.get('icon'))]:
            add(m.get(local), source)
        variants = [('front', m.get('spine', {}).get('front'), s.get('battleSpine', {}).get('front')),
                    ('back', m.get('spine', {}).get('back'), s.get('battleSpine', {}).get('back'))] if group == 'chars' else [('', m.get('spine'), s.get('battleSpine'))]
        for _, target, origin in variants:
            if not target or not origin:
                continue
            for kind in ['skel', 'atlas']:
                add(target.get(kind), origin.get(kind))
            textures = target.get('textures', [])
            if len(textures) == 1:
                add(textures[0], origin.get('png'))

if '--plan' in sys.argv[3:]:
    print(json.dumps({rel:url for rel,url in files.items() if not (root / "public" / rel).is_file()}));raise SystemExit(0)

def fetch(pair):
    rel, url = pair
    dest = root / 'public' / rel
    if dest.is_file() and dest.stat().st_size:
        return 'present'
    try:
        content = urllib.request.urlopen(url, timeout=35).read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + '.priority-part')
        tmp.write_bytes(content)
        tmp.replace(dest)
        return 'downloaded'
    except Exception as exc:
        return f'missing: {rel}: {exc}'

print(f'Priority: {len(used)} unit sprites / {len(files)} files', flush=True)
counts = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    for result in pool.map(fetch, files.items()):
        counts[result.split(':')[0]] = counts.get(result.split(':')[0], 0) + 1
        if result.startswith('missing'):
            print(result, flush=True)
print(json.dumps(counts), flush=True)
