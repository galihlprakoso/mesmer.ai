"""Context — the shared state passed to every module."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from mesmer.core.constants import (
    BUDGET_EXPLOIT_UPPER_RATIO,
    BUDGET_EXPLORE_UPPER_RATIO,
    TARGET_ERROR_MARKERS,
    BudgetMode,
    ContextMode,
    LogEvent,
    ScenarioMode,
)


def is_target_error(reply: str) -> bool:
    """Classify a target reply as a pipeline error vs a genuine response.

    Conservative: empty/whitespace-only replies, and replies whose
    lower-cased text contains any :data:`TARGET_ERROR_MARKERS` substring.
    Anything else is treated as a legitimate target response — even if
    it looks like a refusal, a refusal is real target behaviour.
    """
    if reply is None:
        return True
    s = reply.strip()
    if not s:
        return True
    lowered = s.lower()
    return any(marker in lowered for marker in TARGET_ERROR_MARKERS)

if TYPE_CHECKING:
    from mesmer.core.graph import AttackGraph
    from mesmer.core.registry import Registry
    from mesmer.core.scenario import AgentConfig
    from mesmer.targets.base import Target


@dataclass
class RunTelemetry:
    """Lightweight per-run accumulator for tokens and wall-clock.

    Attached to every :class:`Context` as ``ctx.telemetry``. Filled in
    opportunistically by :meth:`Context.completion` — each successful
    litellm call adds its ``response.usage`` token counts and the time
    spent awaiting the call.

    Exists so benchmarks can report *"{mesmer vs baseline} on llama-8b:
    52% ASR, median 6 turns, 8,400 tokens/trial"* without having to
    re-plumb instrumentation through every sub-module. Failed calls are
    ignored (keeping numbers conservative).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Wall-clock spent inside ``litellm.acompletion`` calls. Real per-run
    # wall-clock is recorded separately in ``runner.execute_run``.
    llm_seconds: float = 0.0
    # Number of successful completions observed. Cheap anomaly detector:
    # zero calls across a full run usually means the retry loop never
    # succeeded.
    n_calls: int = 0

    def add_usage(self, usage: object | None, seconds: float) -> None:
        """Fold one completion's usage numbers into the accumulator.

        Accepts either the Pydantic-style OpenAI ``CompletionUsage`` object
        returned by litellm, a dict with the same keys, or ``None`` (from
        providers that don't emit usage). Missing fields degrade to zero —
        the counter is monotonic either way.
        """
        if seconds > 0:
            self.llm_seconds += seconds
        self.n_calls += 1
        if usage is None:
            return

        def _get(attr: str) -> int:
            if isinstance(usage, dict):
                return int(usage.get(attr, 0) or 0)
            raw = getattr(usage, attr, 0) or 0
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        self.prompt_tokens += _get("prompt_tokens")
        self.completion_tokens += _get("completion_tokens")
        self.total_tokens += _get("total_tokens")


class TurnBudgetExhausted(Exception):
    """Raised when a module exceeds its turn budget."""

    def __init__(self, turns_used: int):
        self.turns_used = turns_used
        super().__init__(f"Turn budget exhausted after {turns_used} turns")


class HumanQuestionTimeout(Exception):
    """Raised when a human doesn't answer a co-op question in time."""


class HumanQuestionBroker:
    """Coordinates ask_human round-trips between the loop and the UI.

    Usage:
        question_id = broker.create_question("What should I try next?")
        # Emit question over transport (WebSocket, etc.)
        answer = await broker.wait_for_answer(question_id, timeout=300)

    The transport layer calls broker.answer(question_id, text) to fulfill.
    """

    def __init__(self, on_question: Callable[[dict], None] | None = None):
        # question_id -> Future[str]
        self._pending: dict[str, asyncio.Future] = {}
        # Optional hook called when a new question is registered.
        # The web layer uses this to push the question over WebSocket.
        self.on_question = on_question

    def create_question(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
        module: str = "",
    ) -> str:
        """Register a question and return its ID. Does not block."""
        qid = uuid.uuid4().hex[:12]
        loop = asyncio.get_event_loop()
        self._pending[qid] = loop.create_future()
        if self.on_question:
            self.on_question({
                "question_id": qid,
                "question": question,
                "options": options or [],
                "context": context,
                "module": module,
                "timestamp": time.time(),
            })
        return qid

    async def wait_for_answer(self, question_id: str, timeout: float = 300.0) -> str:
        fut = self._pending.get(question_id)
        if fut is None:
            raise KeyError(f"No such question: {question_id}")
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise HumanQuestionTimeout(f"No answer for question {question_id}")
        finally:
            self._pending.pop(question_id, None)

    def answer(self, question_id: str, text: str) -> bool:
        """Called by the transport when a human answer arrives. Returns True if matched."""
        fut = self._pending.get(question_id)
        if fut is None or fut.done():
            return False
        fut.set_result(text)
        return True

    def cancel_all(self, reason: str = "cancelled"):
        """Cancel all pending questions (e.g., on run stop)."""
        for qid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(HumanQuestionTimeout(reason))
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)


@dataclass
class Turn:
    """A single exchange with the target.

    Most Turns are ``kind="exchange"``: a real ``sent`` → ``received`` round
    with the target model. CONTINUOUS-mode compression (C9) introduces a
    second kind, ``"summary"``, where the Turn carries a synthetic LLM-
    authored recap of multiple older exchanges that were compressed to fit
    the attacker's context budget. Summary Turns have ``sent=""`` and
    ``received=<summary text>``; ``is_error`` is always False for them.
    Summary Turns can themselves be compressed later (they stack), so the
    field choice is a simple string rather than a two-case enum.
    """

    sent: str
    received: str
    module: str = ""
    timestamp: float = field(default_factory=time.time)
    # P4 — True when ``received`` looks like a target-side pipeline error
    # (timeout, gateway 5xx, rate-limit bounce, empty response), not a
    # real reply from the target model. Judge and tool-result formatting
    # use this to avoid scoring an error as a refusal.
    is_error: bool = False
    # C9 — "exchange" (default) or "summary". Summary turns are compressed
    # blocks of older history; formatters render them as ``[Summary of N
    # earlier turns: ...]`` so attacker + judge can read them plainly.
    kind: str = "exchange"

    def to_dict(self) -> dict:
        return {
            "sent": self.sent,
            "received": self.received,
            "module": self.module,
            "timestamp": self.timestamp,
            "is_error": self.is_error,
            "kind": self.kind,
        }


@dataclass
class ModuleRun:
    """Record of a module execution."""

    name: str
    instruction: str
    result: str
    turns_used: int
    success: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "instruction": self.instruction,
            "result": self.result,
            "turns_used": self.turns_used,
            "success": self.success,
            "timestamp": self.timestamp,
        }


class Context:
    """
    Shared state for all modules. Holds the target, conversation history,
    module execution log, and turn budgets.

    Uses litellm for LLM calls — supports any provider via model string prefix.
    """

    def __init__(
        self,
        target: Target,
        registry: Registry,
        agent_config: AgentConfig,
        objective: str = "",
        success_signals: list[str] | None = None,
        max_turns: int | None = None,
        graph: AttackGraph | None = None,
        run_id: str = "",
        mode: str = ContextMode.AUTONOMOUS.value,
        human_broker: HumanQuestionBroker | None = None,
        plan: str | None = None,
        judge_rubric_additions: str = "",
        target_fresh_session: bool = False,
        attacker_model_override: str = "",
        depth: int = 0,
        scenario_mode: ScenarioMode = ScenarioMode.TRIALS,
        # Internal — set by child()
        _turns: list[Turn] | None = None,
        _module_log: list[ModuleRun] | None = None,
        _target_reset_at: int = 0,
    ):
        self.target = target
        self.registry = registry
        self.agent_config = agent_config
        self.objective = objective
        self.success_signals = success_signals or []
        self.turn_budget = max_turns
        self.turns_used = 0
        self.done = False
        self.run_id = run_id
        # The API key used on the most recent completion() call. Set by
        # completion(), read by the retry loop to cool down rate-limited keys.
        self._last_key_used: str = ""
        self.mode = mode
        self.human_broker = human_broker
        self.plan = plan  # Optional plan.md content (human-authored guidance)
        # Scenario-specific rubric text appended to JUDGE_SYSTEM. Keeps the
        # judge calibrated to the particular attack (e.g. profiling ≠ extraction).
        self.judge_rubric_additions = judge_rubric_additions

        # Attack graph — shared across parent/child
        self.graph: AttackGraph | None = graph

        # Track messages for current module execution (for judge)
        self.current_messages_sent: list[str] = []
        self.current_responses: list[str] = []

        # Shared across parent/child — same list reference
        self.turns: list[Turn] = _turns if _turns is not None else []
        self.module_log: list[ModuleRun] = _module_log if _module_log is not None else []

        # P0 — target memory reset tracking.
        #   target_fresh_session: True when this module entered with a fresh
        #       target session (target has no memory of prior turns). Drives
        #       prompt framing in run_react_loop.
        #   _target_reset_at: global turn index at which the most recent
        #       target.reset() fired. format_turns() uses this to only show
        #       turns from the target's current session, so the attacker LLM
        #       doesn't hallucinate continuity the target can't actually see.
        self.target_fresh_session: bool = target_fresh_session
        self._target_reset_at: int = _target_reset_at

        # P5 — when a scenario declares an attacker-model ensemble, each
        # child context is bound to a specific model chosen round-robin by
        # ctx.run_module(). Empty string means "use agent_config.model".
        # The judge role (see completion(role="judge")) bypasses this and
        # always uses agent_config.effective_judge_model.
        self.attacker_model_override: str = attacker_model_override

        # P6 — nesting depth for log display. Root context has depth 0;
        # every ctx.child() returns depth+1. The CLI renderer uses this
        # to disambiguate which module's iteration counter it's looking at.
        self.depth: int = depth

        # Scenario-level execution mode. Determines whether sub-modules are
        # independent trials (default, TRIALS) or moves inside one continuous
        # target conversation (CONTINUOUS). Propagated unchanged to child
        # contexts. Distinct from ``self.mode`` — that controls human co-op
        # framing (autonomous vs co-op), this controls target memory semantics.
        self.scenario_mode: ScenarioMode = scenario_mode

        # Per-run telemetry accumulator (tokens + LLM wall-clock). The
        # reference is *shared* across parent / child contexts so a single
        # scenario rolls up all sub-module calls into one set of numbers.
        # child() propagates ``self.telemetry`` explicitly; root contexts
        # create a fresh one.
        self.telemetry: RunTelemetry = RunTelemetry()

    @property
    def agent_model(self) -> str:
        """Currently-bound attacker model (rotation override wins)."""
        return self.attacker_model_override or self.agent_config.model

    def _resolve_model(self, role: str) -> str:
        """Pick the model to use for an LLM call based on its role.

        - ``attacker`` (default): the rotation-assigned attacker model if
          bound on this context, otherwise the config's base attacker model.
        - ``judge``: the scenario's judge model (stable across rotation).

        Unknown roles are treated as attacker for forward-compat.
        """
        if role == "judge":
            return self.agent_config.effective_judge_model
        return self.attacker_model_override or self.agent_config.model

    async def completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        role: str = "attacker",
    ):
        """
        Call the LLM via litellm. Returns an OpenAI-compatible response.
        Supports any provider: openrouter/, anthropic/, openai/, gemini/, etc.

        ``role`` picks which model to use (see :meth:`_resolve_model`).
        Keep it ``"attacker"`` for ReAct-loop LLM calls; pass ``"judge"``
        from judge/refinement code so scoring doesn't drift with the
        attacker-model rotation.

        Side effect: `self._last_key_used` is set to whichever API key was
        selected for this call (or "" when none). The retry loop reads it
        to cool down a specific key when a rate-limit error fires.
        """
        import litellm

        # Suppress litellm's noisy logging
        litellm.suppress_debug_info = True

        kwargs: dict = {
            "model": self._resolve_model(role),
            "messages": messages,
            "temperature": self.agent_config.temperature,
        }

        # API key — rotates through keys (round-robin, skipping cooled-down ones)
        key = self.agent_config.next_key()
        self._last_key_used = key  # exposed to _completion_with_retry
        if key:
            kwargs["api_key"] = key

        # Custom base URL (e.g., self-hosted vLLM, LocalAI)
        if self.agent_config.api_base:
            kwargs["api_base"] = self.agent_config.api_base

        # Max tokens
        if self.agent_config.max_tokens:
            kwargs["max_tokens"] = self.agent_config.max_tokens

        # Tools (function calling)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Extra params (top_p, etc.)
        kwargs.update(self.agent_config.extra)

        t0 = time.monotonic()
        response = await litellm.acompletion(**kwargs)
        elapsed = time.monotonic() - t0

        # Fold usage into the run's telemetry. Providers that don't emit
        # usage (some Ollama builds, some relays) leave ``usage`` as None —
        # ``add_usage`` handles that case gracefully.
        try:
            self.telemetry.add_usage(getattr(response, "usage", None), elapsed)
        except Exception:
            # Telemetry is observability, not correctness — never let a
            # metrics bug fail the actual run.
            pass

        return response

    async def send(self, message: str, module_name: str = "") -> str:
        """Send a message to the target. Respects turn budget.

        Pipeline errors (timeouts, gateway 5xx, rate-limit bounces, empty
        replies) are tagged on the resulting Turn as ``is_error=True`` so
        the attacker LLM and judge can distinguish them from genuine target
        refusals. The reply string is still returned unchanged — callers
        that care about the distinction read ``ctx.turns[-1].is_error``.
        """
        if self.turn_budget is not None and self.turns_used >= self.turn_budget:
            raise TurnBudgetExhausted(self.turns_used)

        reply = await self.target.send(message)
        error = is_target_error(reply)
        self.turns.append(
            Turn(sent=message, received=reply, module=module_name, is_error=error)
        )
        self.turns_used += 1

        # Track for judge evaluation
        self.current_messages_sent.append(message)
        self.current_responses.append(reply)

        return reply

    async def generate(self, prompt: str, temperature: float = 0.7) -> str:
        """Use the LLM to generate content (for modules that craft messages)."""
        response = await self.completion(
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    async def run_module(
        self,
        name: str,
        instruction: str,
        max_turns: int | None = None,
        log=None,
    ) -> str:
        """Delegate to a sub-module with a scoped turn budget.

        Reset-target semantics depend on :attr:`scenario_mode`:

        - ``TRIALS`` (default): when the sub-module declares ``reset_target:
          true`` in its YAML, we reset the shared target BEFORE running it
          so the target sees a fresh session. This breaks the target's
          compounding memory across sibling modules.
        - ``CONTINUOUS``: the whole run is one live conversation. We NEVER
          reset, regardless of what the module YAML says. A declared
          ``reset_target: true`` is logged as :data:`LogEvent.MODE_OVERRIDE`
          and ignored — breaking continuity would defeat the point of
          continuous mode.
        """
        from mesmer.core.loop import run_react_loop

        module = self.registry.get(name)
        if module is None:
            return f"Error: module '{name}' not found in registry"

        fresh_session = False
        if module.reset_target:
            if self.scenario_mode == ScenarioMode.CONTINUOUS:
                # Continuous mode forbids target resets — the single target
                # conversation IS the point. Warn once per occurrence so
                # scenario authors notice, but keep running.
                if log is not None:
                    log(
                        LogEvent.MODE_OVERRIDE.value,
                        f"module '{name}' declares reset_target: true but "
                        "scenario mode is CONTINUOUS — reset skipped, "
                        "conversation continues."
                    )
            else:
                try:
                    await self.target.reset()
                    self._target_reset_at = len(self.turns)
                    fresh_session = True
                    if log is not None:
                        log(LogEvent.TARGET_RESET.value, f"Fresh target session for '{name}'")
                except Exception as e:
                    # A reset failure shouldn't kill the run — log and continue
                    # with the existing session. The sub-module will still run
                    # but against a target that remembers everything.
                    if log is not None:
                        log(LogEvent.TARGET_RESET_ERROR.value, f"reset failed for '{name}': {e}")

        # P5 — rotate the attacker model round-robin when an ensemble is
        # declared. No-op (returns the single model) otherwise.
        chosen_model = self.agent_config.next_attacker_model()

        child = self.child(
            max_turns=max_turns,
            target_fresh_session=fresh_session,
            attacker_model_override=chosen_model,
        )
        result = await run_react_loop(module, child, instruction, log=log)

        self.module_log.append(
            ModuleRun(
                name=name,
                instruction=instruction,
                result=result,
                turns_used=child.turns_used,
            )
        )
        return result

    def reset_current_tracking(self) -> None:
        """Reset per-module message tracking (called before each module run)."""
        self.current_messages_sent = []
        self.current_responses = []

    @property
    def budget_mode(self) -> str:
        """Explore/exploit/conclude based on budget consumption."""
        if self.turn_budget is None:
            return BudgetMode.EXPLORE.value
        ratio = self.turns_used / self.turn_budget
        if ratio < BUDGET_EXPLORE_UPPER_RATIO:
            return BudgetMode.EXPLORE.value
        elif ratio < BUDGET_EXPLOIT_UPPER_RATIO:
            return BudgetMode.EXPLOIT.value
        return BudgetMode.CONCLUDE.value

    def child(
        self,
        max_turns: int | None = None,
        target_fresh_session: bool = False,
        attacker_model_override: str = "",
    ) -> Context:
        """Create a child context — shares target + turns + graph, own budget.

        ``target_fresh_session`` is passed through so the child's run_react_loop
        can reframe its prompt: the target has no memory of prior turns, so
        the attacker should treat sibling-module history as intel rather than
        shared context with the target.

        ``attacker_model_override`` binds the child to a specific attacker
        model (used for MODEL ensemble rotation). Empty string means inherit
        the parent's override (or the config's base model if no override).
        """
        child = Context(
            target=self.target,
            registry=self.registry,
            agent_config=self.agent_config,  # shared config
            objective=self.objective,
            success_signals=self.success_signals,
            max_turns=max_turns,
            graph=self.graph,               # shared graph
            run_id=self.run_id,
            mode=self.mode,                 # mode inherited
            human_broker=self.human_broker, # broker shared
            plan=self.plan,                 # plan shared
            judge_rubric_additions=self.judge_rubric_additions,  # shared
            target_fresh_session=target_fresh_session,
            attacker_model_override=attacker_model_override or self.attacker_model_override,
            depth=self.depth + 1,           # deeper in the module tree
            scenario_mode=self.scenario_mode,  # inherit CONTINUOUS/TRIALS
            _turns=self.turns,              # same list reference
            _module_log=self.module_log,    # shared log
            _target_reset_at=self._target_reset_at,
        )
        # Telemetry rolls up to the run — every sub-module's LLM usage
        # must land in the same accumulator the caller reads post-run.
        child.telemetry = self.telemetry
        return child

    async def ask_human(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
        module: str = "",
        timeout: float = 300.0,
    ) -> str:
        """Ask the human a question and await their answer.

        Only usable in co-op mode with a broker attached. In autonomous mode,
        returns an empty string so modules that optimistically call it degrade
        gracefully rather than blocking forever.
        """
        if self.mode != ContextMode.CO_OP or self.human_broker is None:
            return ""
        qid = self.human_broker.create_question(
            question=question,
            options=options,
            context=context,
            module=module,
        )
        try:
            return await self.human_broker.wait_for_answer(qid, timeout=timeout)
        except HumanQuestionTimeout:
            return "(no response from human — continue with best judgement)"

    def format_turns(self, last_n: int = 10) -> str:
        """Format recent conversation for LLM consumption.

        Summary turns (C9 — compressed blocks of older history) are rendered
        as an inline ``[Summary: ...]`` line so both the attacker LLM and the
        judge can read them plainly without a new schema.
        """
        recent = self.turns[-last_n:] if self.turns else []
        if not recent:
            return "(no conversation yet)"

        lines = []
        for turn in recent:
            if getattr(turn, "kind", "exchange") == "summary":
                lines.append(f"[Summary of compressed earlier turns: {turn.received}]")
                lines.append("")
                continue
            prefix = f"[{turn.module}] " if turn.module else ""
            lines.append(f"{prefix}You: {turn.sent}")
            lines.append(f"Target: {turn.received}")
            lines.append("")
        return "\n".join(lines).strip()

    def format_session_turns(self, last_n: int = 10) -> str:
        """Format turns from the target's CURRENT session only.

        Excludes turns that happened before the most recent target.reset().
        Use this when the attacker needs to see what the target actually
        remembers — anything earlier has been wiped from the target's side.
        Summary turns (C9) render inline just like in :meth:`format_turns`.
        """
        session_turns = self.turns[self._target_reset_at:]
        recent = session_turns[-last_n:] if session_turns else []
        if not recent:
            return "(no conversation in this target session yet)"

        lines = []
        for turn in recent:
            if getattr(turn, "kind", "exchange") == "summary":
                lines.append(f"[Summary of compressed earlier turns: {turn.received}]")
                lines.append("")
                continue
            prefix = f"[{turn.module}] " if turn.module else ""
            lines.append(f"{prefix}You: {turn.sent}")
            lines.append(f"Target: {turn.received}")
            lines.append("")
        return "\n".join(lines).strip()

    def format_module_log(self, last_n: int = 10) -> str:
        """Format recent module runs for LLM consumption."""
        recent = self.module_log[-last_n:] if self.module_log else []
        if not recent:
            return "(no modules run yet)"

        lines = []
        for run in recent:
            lines.append(
                f"- {run.name} (instruction: {run.instruction}, "
                f"turns: {run.turns_used}): {run.result[:200]}"
            )
        return "\n".join(lines)

    def to_report(self) -> dict:
        """Generate a structured report of the full run."""
        return {
            "objective": self.objective,
            "success_signals": self.success_signals,
            "total_turns": len(self.turns),
            "module_trace": [r.to_dict() for r in self.module_log],
            "conversation": [t.to_dict() for t in self.turns],
        }
