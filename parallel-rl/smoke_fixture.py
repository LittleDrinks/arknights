"""Real upstream-teacher BC fixture, plus saved-checkpoint verification for smoke.py."""
import json
from pathlib import Path
import sys

import train
from rl.learner import bc_update


def main():
    out = Path(sys.argv[2])
    if sys.argv[1] == 'prepare':
        train.torch.manual_seed(73)
        with train.Environment(timeout=5) as env:
            demo = env.request('demo', seed=7, difficulty='NORMAL')
        if not demo['terminal']['done'] or not demo['samples']:
            raise RuntimeError('No completed real teacher demonstration')
        features = demo['samples'][0]['features']
        model = train.ActorCritic(len(features['global']), len(features['candidates'][0]), 8)
        metrics, optimizer = bc_update(model, demo['samples'][:4], epochs=1, batch_size=4)
        train.save_checkpoint(out / 'bc.pt', model, demo['schema'], demo['rules'],
                              {'algorithm': 'BC', 'metrics': metrics, 'teacherSeed': 7}, optimizer)
        config = json.loads((train.ROOT / 'rl/config.json').read_text())
        config.update(hidden=8, seed=2001, epochs=1, batch_size=4, rollout_steps=4, prep_budget=1)
        (out / 'config.json').write_text(json.dumps(config))
        print(json.dumps({'teacherSamples': len(demo['samples']), 'bc': metrics}))
    elif sys.argv[1] == 'verify':
        for workers, steps in ((1, 4), (2, 17)):
            path = out / f'w{workers}.pt'
            if not path.exists():
                continue
            model, checkpoint = train.load_checkpoint(path)
            report = checkpoint['metadata']['report']
            assert report['totalSteps'] == steps
            assert train.digest(model) == report['parameterSHA256']
            assert model.dimensions[2] == 8
            print(json.dumps({'workers': workers, 'savedParametersMatchReport': True}))
    else:
        raise ValueError('prepare or verify required')


if __name__ == '__main__':
    main()
