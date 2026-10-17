"""Private, fixed CPU training snapshots; never an inference validation bypass."""
from contextlib import contextmanager
from types import MappingProxyType

import dataset
import model as models
import train
from common import require, schema_dimensions, sha, validate_label
from model import torch, Categorical


class _TensorCache:
    """Own tensors exclusively: no tensor/view escapes and batches are fresh copies.

    Samples expose only immutable, identity-bearing handles and immutable masks.
    Original dicts are NOT cache keys. Strong references to every handle prevent
    id reuse. Python private attributes are an ownership contract, not a sandbox
    against code deliberately accessing/mutating implementation internals.
    """

    def __init__(self, samples, dimensions):
        require(bool(samples), 'Empty cache')
        for sample in samples:
            validate_label(sample, dimensions)
        self.__dimensions = tuple(dimensions)
        self.__globals = torch.tensor([s['features']['global'] for s in samples], dtype=torch.float32)
        self.__candidates = torch.tensor([c for s in samples for c in s['features']['candidates']],
                                         dtype=torch.float32)
        self.__mask = torch.tensor([m for s in samples for m in s['features']['mask']], dtype=torch.bool)
        offsets, handles, offset = [], [], 0
        for sample in samples:
            mask = tuple(sample['features']['mask'])
            # Separate handles even when input feature references are repeated.
            handles.append(MappingProxyType({'mask': mask}))
            offsets.append((offset, len(mask)))
            offset += len(mask)
        self.__handles = tuple(handles)
        self.__indices = {id(handle): i for i, handle in enumerate(handles)}
        self.__offsets = tuple(offsets)
        self.__samples = tuple(MappingProxyType({'features': handle, 'action': sample['action']})
                               for handle, sample in zip(handles, samples))
        self.tensor_bytes = sum(t.numel() * t.element_size()
                                for t in (self.__globals, self.__candidates, self.__mask))
        self.__active = False

    @property
    def samples(self):
        return self.__samples

    def _forward(self, model, original, features):
        require(bool(features), 'Empty feature batch')
        indices = [self.__indices.get(id(f)) for f in features]
        if all(i is None for i in indices):
            return original(features)  # Unknown dicts still get full original validation.
        require(all(i is not None and self.__handles[i] is f for i, f in zip(indices, features)),
                'Mixed private and external features')
        require(self.__active and model.training and torch.is_grad_enabled(),
                'Private cache handles are training-only')
        require(tuple(model.dimensions[:2]) == self.__dimensions, 'Cache schema dimensions')
        width = max(self.__offsets[i][1] for i in indices)
        candidates = torch.zeros(len(indices), width, self.__dimensions[1], dtype=torch.float32)
        mask = torch.zeros(len(indices), width, dtype=torch.bool)
        for row, index in enumerate(indices):
            offset, size = self.__offsets[index]
            candidates[row, :size] = self.__candidates[offset:offset + size]
            mask[row, :size] = self.__mask[offset:offset + size]
        h = model.state(self.__globals[indices])
        # Exactly the original classes' equations, including batch-local width,
        # zero padding, masked -inf logits, Categorical normalization and critic.
        if type(model) is models.ConcatBaseline:
            logits = model.actor(torch.cat([h[:, None, :].expand(-1, width, -1), candidates], dim=-1)).squeeze(-1)
        else:
            require(type(model) is models.Interaction, 'Unsupported cached model')
            logits = model.product_score(h[:, None, :] * model.candidate(candidates)).squeeze(-1)
        return Categorical(logits=logits.masked_fill(~mask, -torch.inf)), model.critic(h).squeeze(-1)

    @contextmanager
    def training_models(self):
        """Single-threaded new-process context; patch only train's factory binding.

        No class, checkpoint loader, dataset loader or existing model is patched.
        Each newly made instance has its original forward restored even on error.
        """
        require(not self.__active and train.make_model is models.make_model, 'Nested/concurrent cache context')
        original_factory = train.make_model
        created = []

        def factory(arm, dimensions):
            model = original_factory(arm, dimensions)
            require(tuple(dimensions[:2]) == self.__dimensions, 'Cache/model dimensions differ')
            original = model.forward
            model.forward = lambda features: self._forward(model, original, features)
            created.append(model)
            return model

        self.__active = True
        train.make_model = factory
        try:
            yield
        finally:
            train.make_model = original_factory
            for model in created:
                del model.forward
            self.__active = False


def load_cache(data_path, manifest_path, **admission):
    """Only public cache construction: unchanged whole-file admission before freeze."""
    before = (sha(data_path), sha(manifest_path))
    samples, admitted = dataset.load_dataset(data_path, manifest_path, **admission)
    require(before == (admitted['datasetSHA256'], admitted['manifestSHA256']) ==
            (sha(data_path), sha(manifest_path)), 'Input changed during admission')
    cache = _TensorCache(samples, schema_dimensions(admitted['schema']))
    return cache, admitted
