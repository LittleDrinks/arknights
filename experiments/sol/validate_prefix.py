"""Read-only offline regression over this author's saved PUBLIC protocol prefix.

Does not construct a session, advance combat, or claim a game outcome.
Usage: python3 -m experiments.sol.validate_prefix <episode-directory>
"""
import contextlib
import io
import json
from pathlib import Path
import sys
from experiments.sol.agent import Policy, pieces


class RecordedLookups:
    def __init__(self, responses):
        self.catalog = {}
        for response in responses:
            result = response.get('result', {})
            if 'kind' in result and 'entries' in result:
                for entry in result['entries']:
                    self.catalog[(result['kind'], entry['id'])] = entry

    def lookup(self, q):
        return {'entries': [e for (kind, id), e in self.catalog.items()
                            if kind == q['kind'] and q.get('id', id) == id]}


def main():
    root = Path(sys.argv[1])
    data = json.loads((root / 'result.json').read_text())
    state = data['prefixState']
    responses = [json.loads(line) for line in (root / 'trajectory/protocol.jsonl').read_text().splitlines()]
    p = Policy(RecordedLookups(responses))
    p.pub = state['public']
    p.transition(state)
    grant = [u for u in pieces(state['own']) if u['kind'] == 'chess' and not p.meta(u)]
    if state['own']['bandId'] == 'band_pith' and len(grant) == 1:
        p.pith_uid = grant[0]['uid']
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        p.report(state, 'offline-prefix-regression')
    report = json.loads(output.getvalue())
    assert report['LP'] == state['own']['lp']
    assert report['bonds'] == state['own']['bonds']
    action = p.arrange(state)
    assert action and action['type'] == 'move', 'Do not ready while ranged candidates can still deploy'
    if action['to']['area'] == 'board':
        assert any((c['row'], c['col']) == (action['to']['row'], action['to']['col'])
                   for c in state['own']['deployment']['cells'])
    print(json.dumps({'offline_prefix': str(root), 'report_equipment_ok': True,
                      'deployment_continues': action, 'battle_executed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
