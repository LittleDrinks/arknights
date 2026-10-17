"""Dedicated BC architectures and a fail-closed, weights-only checkpoint loader."""
from common import ROOT, RULES, require, schema_dimensions, validate_features
import sys

sys.path.insert(0, str(ROOT / 'rl'))
import learner as L
import torch
from torch import nn
from torch.distributions import Categorical

FORMAT = 'interaction-bc/v1'
ARMS = ('concat-baseline', 'interaction')
DESCRIPTIONS = {
    'concat-baseline': 'h=tanh(Ws*s+bs); score=w*tanh(Wa*[h,c]+ba)+b; original ActorCritic',
    'interaction': 'h=tanh(Ws*s+bs); z=tanh(Wc*c+bc); score=w*(h*z)+b',
}


class ConcatBaseline(L.ActorCritic):
    def forward(self, features):
        require(bool(features), 'Empty feature batch')
        for f in features:
            validate_features(f, self.dimensions)
        return super().forward(features)


class Interaction(ConcatBaseline):
    def __init__(self, global_dim, candidate_dim, hidden=128):
        super().__init__(global_dim, candidate_dim, hidden)
        del self.actor
        self.candidate = nn.Sequential(nn.Linear(candidate_dim, hidden), nn.Tanh())
        self.product_score = nn.Linear(hidden, 1)

    def forward(self, features):
        require(bool(features), 'Empty feature batch')
        for f in features:
            validate_features(f, self.dimensions)
        width = max(len(f['candidates']) for f in features)
        candidates = torch.zeros(len(features), width, self.dimensions[1])
        mask = torch.zeros(len(features), width, dtype=torch.bool)
        for i, f in enumerate(features):
            k = len(f['candidates'])
            candidates[i, :k] = torch.tensor(f['candidates'], dtype=torch.float32)
            mask[i, :k] = torch.tensor(f['mask'], dtype=torch.bool)
        h = self.state(torch.tensor([f['global'] for f in features], dtype=torch.float32))
        logits = self.product_score(h[:, None, :] * self.candidate(candidates)).squeeze(-1)
        return Categorical(logits=logits.masked_fill(~mask, -torch.inf)), self.critic(h).squeeze(-1)


def make_model(arm, dimensions):
    require(arm in ARMS, 'Unknown architecture')
    require(isinstance(dimensions, list) and len(dimensions) == 3 and
            all(type(x) is int and 1 <= x <= 32768 for x in dimensions) and dimensions[2] <= 1024,
            'Invalid model dimensions')
    require(dimensions[0] * dimensions[2] + (dimensions[1] + dimensions[2]) * dimensions[2] < 20_000_000,
            'Model exceeds dedicated loader size limit')
    return (ConcatBaseline if arm == ARMS[0] else Interaction)(*dimensions)


def parameter_counts(model):
    # Critic is retained to match the original API; it receives no BC gradient.
    total = sum(p.numel() for p in model.parameters())
    critic = sum(p.numel() for p in model.critic.parameters())
    return {'total': total, 'bcActive': total - critic, 'unusedCritic': critic}


def save_checkpoint(path, model, arm, schema, metadata):
    # Deliberately omit original loader's 'dimensions' and 'model' keys, for BOTH arms.
    torch.save({'format': FORMAT, 'architecture': {'name': arm, 'version': 1,
                'equation': DESCRIPTIONS[arm], 'dimensions': model.dimensions},
                'policyState': model.state_dict(), 'schema': schema, 'rules': RULES,
                'metadata': metadata, 'parameterCounts': parameter_counts(model)}, path)


def load_checkpoint(path):
    from pathlib import Path
    require(Path(path).stat().st_size <= 128 * 1024 * 1024, 'Checkpoint size limit')
    ckpt = torch.load(path, map_location='cpu', weights_only=True)
    require(isinstance(ckpt, dict) and ckpt.get('format') == FORMAT, 'Dedicated BC checkpoint required')
    arch = ckpt['architecture']
    require(arch['name'] in ARMS and arch['version'] == 1 and arch['equation'] == DESCRIPTIONS[arch['name']],
            'Unknown architecture contract')
    require(ckpt['rules'] == RULES and arch['dimensions'][:2] == schema_dimensions(ckpt['schema']),
            'Checkpoint schema/rules mismatch')
    model = make_model(arch['name'], arch['dimensions'])
    model.load_state_dict(ckpt['policyState'], strict=True)
    require(all(torch.isfinite(p).all().item() for p in model.parameters()), 'Non-finite checkpoint')
    require(ckpt['parameterCounts'] == parameter_counts(model), 'Parameter count mismatch')
    model.eval()
    return model, ckpt
