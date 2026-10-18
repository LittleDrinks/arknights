"""Evaluate trusted play(game) scripts in isolated, bounded subprocesses.

Every attempt is retained in a fresh run directory; runs do not resume.
Training remains separate. Protected seeds 100001..100064 cannot be evaluated.
Team and learner victories are independently validated against saved terminals.
"""
from .runner import Run, evaluate, sha_file, verify_run

def submit(script, *, root=None, port=38181, **config):
    """Enqueue trusted local policy code; return the persistent job and dashboard URL."""
    import json
    from pathlib import Path
    import urllib.request
    from arknights.replay import preview
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    address = preview(root=root, port=port)
    script = Path(script)
    script = script if script.is_absolute() else root / script
    request = urllib.request.Request(address + '/api/evaluations',
        data=json.dumps({'script': str(script), **config}, allow_nan=False).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=10) as response:
        job = json.load(response)
    return {**job, 'url': address + '/evaluations?job=' + job['id']}


__all__ = ['Run', 'evaluate', 'submit', 'sha_file', 'verify_run']
