#!/usr/bin/env python3
"""Small real-engine BC -> PPO -> checkpoint -> deterministic full evaluation smoke."""
import json
from pathlib import Path
import time
import torch
from learner import (Environment, ActorCritic, bc_update, collect_rollout, ppo_update, gae,
                     vector, save_checkpoint, load_checkpoint, evaluate, validate_checkpoint, HERE)


def main():
    start = time.monotonic()
    torch.manual_seed(20261018)
    config = json.loads((HERE / 'config.json').read_text())
    config.update(epochs=1, rollout_steps=64, batch_size=8, prep_budget=8)
    out = HERE / 'artifacts'
    out.mkdir(exist_ok=True)
    advantage, returns = gae([1., 2.], [False, True], [0.5, 0.25], 999., gamma=1., lam=1.)
    assert torch.allclose(returns, torch.tensor([3., 2.]))
    _, truncated = gae([1.], [False], [0.5], 2., gamma=0.5, lam=1.)
    assert torch.allclose(truncated, torch.tensor([2.]))
    with Environment(timeout=30) as env:
        demo = env.request('demo', seed=7, difficulty='NORMAL')
        assert demo['terminal']['done'] and demo['terminal']['engineErrors'] == 0
        samples = demo['samples'][:32]
        assert samples
        f = samples[0]['features']
        model = ActorCritic(len(f['global']), len(f['candidates'][0]), config['hidden'])
        # Padding and false masks have exactly zero probability, not failed/no-op execution.
        short = {'global': f['global'], 'candidates': f['candidates'][:1], 'mask': [True]}
        distribution, _ = model([f, short])
        assert torch.equal(distribution.probs[1, 1:], torch.zeros_like(distribution.probs[1, 1:]))
        pre_actor = torch.cat([p.detach().flatten() for p in model.actor.parameters()]).clone()
        bc, optimizer = bc_update(model, samples, epochs=1, batch_size=8)
        bc_actor_delta = (torch.cat([p.detach().flatten() for p in model.actor.parameters()]) - pre_actor).norm().item()
        assert bc['parameterDeltaL2'] > 0 and bc_actor_delta > 0
        save_checkpoint(out / 'bc.pt', model, demo['schema'], demo['rules'], {'algorithm': 'BC', 'metrics': bc}, optimizer)
        counter = 20
        def reset():
            nonlocal counter
            counter += 1
            return env.request('reset', seed=counter, difficulty='NORMAL', budget=config['prep_budget'])
        state = reset()
        optimizer = torch.optim.Adam(model.parameters(), lr=config['lr'])
        rollout, bootstrap, state, episodes = collect_rollout(env, model, state, 64, reset)
        with torch.no_grad():
            behavior, _ = model(rollout['features'])
            recomputed = behavior.log_prob(torch.tensor(rollout['actions']))
            assert torch.allclose(recomputed, torch.tensor(rollout['logprobs']), atol=2e-6)
        pre_actor = torch.cat([p.detach().flatten() for p in model.actor.parameters()]).clone()
        pre_critic = torch.cat([p.detach().flatten() for p in model.critic.parameters()]).clone()
        ppo = ppo_update(model, optimizer, rollout, bootstrap, config)
        ppo_actor_delta = (torch.cat([p.detach().flatten() for p in model.actor.parameters()]) - pre_actor).norm().item()
        ppo_critic_delta = (torch.cat([p.detach().flatten() for p in model.critic.parameters()]) - pre_critic).norm().item()
        assert ppo_actor_delta > 0 and ppo_critic_delta > 0
        save_checkpoint(out / 'ppo.pt', model, demo['schema'], demo['rules'], {'algorithm': 'PPO', 'metrics': ppo}, optimizer)
        loaded, checkpoint = load_checkpoint(out / 'ppo.pt')
        validate_checkpoint(checkpoint, env.request('reset', seed=4001, difficulty='NORMAL', budget=8))
        assert torch.equal(vector(model), vector(loaded))
        # Full episodes to genuine engine terminal, also after load, same unseen-in-this-smoke seed.
        first = evaluate(env, model, 4001, 'NORMAL', budget=8, trace_path=out / 'evaluation.json')
        second = evaluate(env, loaded, 4001, 'NORMAL', budget=8)
        assert first == second
        report = {'rules': demo['rules'], 'torchVersion': torch.__version__, 'torchThreads': torch.get_num_threads(),
                  'teacher': {'seed': 7, 'difficulty': 'NORMAL', 'samples': len(demo['samples']),
                              'rejectedLabels': demo['rejectedLabels'], 'victory': demo['terminal']['victory'],
                              'rounds': demo['terminal']['rounds'], 'engineErrors': demo['terminal']['engineErrors']},
                  'BC': {**bc, 'actorDeltaL2': bc_actor_delta},
                  'PPO': {**ppo, 'actorDeltaL2': ppo_actor_delta, 'criticDeltaL2': ppo_critic_delta,
                          'completedEpisodesDuringRollout': episodes},
                  'evaluation': first, 'saveLoadIdentical': True, 'evaluationRepeatedIdentically': True,
                  'parameters': sum(p.numel() for p in model.parameters()), 'seconds': time.monotonic() - start}
        (out / 'smoke.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
