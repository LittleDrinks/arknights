"""Evaluate trusted play(game) scripts in isolated, bounded subprocesses.

Every attempt is retained in a fresh run directory; V1 does not resume runs.
Training remains separate. Protected seeds 100001..100064 cannot be evaluated.
"""
from .runner import Run, evaluate, sha_file, verify_run

__all__ = ['Run', 'evaluate', 'sha_file', 'verify_run']
