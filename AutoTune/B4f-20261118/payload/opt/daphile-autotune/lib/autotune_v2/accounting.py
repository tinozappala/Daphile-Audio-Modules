"""Version boundary: legacy scheduling observations are historical only."""
try:from trace_feed import ACCOUNTING_VERSION
except ImportError:from ..trace_feed import ACCOUNTING_VERSION


def issues(observation):
    if not isinstance(observation,dict) or not isinstance(observation.get('scheduler_trace'),dict):
        return ['Versioned scheduler accounting unavailable']
    if observation['scheduler_trace'].get('accounting_version')!=ACCOUNTING_VERSION:
        return ['Scheduler accounting version is historical or unsupported']
    return []

__release_signature__ = 'Tino Zappala'
