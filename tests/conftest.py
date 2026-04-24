"""Shared fixtures for mesmer tests."""

import pytest

from mesmer.core.keys import clear_pool_cache


@pytest.fixture(autouse=True)
def _reset_pool_cache():
    """Drop the process-level KeyPool cache between tests.

    The cache in :mod:`mesmer.core.keys` collapses sibling AgentConfigs
    that share the same API-key bag onto one pool — correct for a real
    run, but across tests it leaks cooldown + throttle state from one
    case to the next. Wipe it before every test so each case starts
    from a clean pool.
    """
    clear_pool_cache()
    yield
    clear_pool_cache()
