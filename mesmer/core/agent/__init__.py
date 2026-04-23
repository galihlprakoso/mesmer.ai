"""Attacker-agent runtime: loop, retry, tools, prompts, judge, compressor.

Public surface:

- :func:`run_react_loop` — the universal ReAct engine.
- :data:`LogFn` — ``(event, detail) → None`` callback type.

``RETRY_DELAYS`` is re-exported from :mod:`mesmer.core.constants` so tests
can monkey-patch ``mesmer.core.agent.RETRY_DELAYS`` and have the change
picked up by :func:`mesmer.core.agent.retry._completion_with_retry` via its
late-bound import.

All other private helpers (``_build_graph_context``, ``_update_graph``,
``_reflect_and_expand``, ``_judge_module_result``, ``_find_missed_frontier``,
``_completion_with_retry``, ``_cool_down_key_for``, ``_is_rate_limit_error``)
are re-exported here because tests import them directly by name.
"""

from mesmer.core.constants import RETRY_DELAYS  # re-exported for test monkey-patching

from mesmer.core.agent.engine import LogFn, _noop_log, run_react_loop
from mesmer.core.agent.evaluation import (
    _format_prior_turns_for_judge,
    _judge_module_result,
    _reflect_and_expand,
    _update_graph,
)
from mesmer.core.agent.prompt import (
    _build_graph_context,
    _budget_banner,
    _budget_suffix,
    _find_missed_frontier,
)
from mesmer.core.agent.prompts import CONTINUATION_PREAMBLE
from mesmer.core.agent.retry import (
    _completion_with_retry,
    _cool_down_key_for,
    _is_rate_limit_error,
)


__all__ = [
    # Public
    "LogFn",
    "RETRY_DELAYS",
    "run_react_loop",
    "CONTINUATION_PREAMBLE",
    # Private helpers tests import directly
    "_build_graph_context",
    "_budget_banner",
    "_budget_suffix",
    "_completion_with_retry",
    "_cool_down_key_for",
    "_find_missed_frontier",
    "_format_prior_turns_for_judge",
    "_is_rate_limit_error",
    "_judge_module_result",
    "_noop_log",
    "_reflect_and_expand",
    "_update_graph",
]
