"""Context — the shared state passed to every module."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from mesmer.core.graph import AttackGraph
    from mesmer.core.registry import Registry
    from mesmer.core.scenario import AgentConfig
    from mesmer.targets.base import Target


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
    """A single exchange with the target."""

    sent: str
    received: str
    module: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "sent": self.sent,
            "received": self.received,
            "module": self.module,
            "timestamp": self.timestamp,
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
        mode: str = "autonomous",
        human_broker: HumanQuestionBroker | None = None,
        plan: str | None = None,
        judge_rubric_additions: str = "",
        target_fresh_session: bool = False,
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

    @property
    def agent_model(self) -> str:
        return self.agent_config.model

    async def completion(self, messages: list[dict], tools: list[dict] | None = None):
        """
        Call the LLM via litellm. Returns an OpenAI-compatible response.
        Supports any provider: openrouter/, anthropic/, openai/, gemini/, etc.

        Side effect: `self._last_key_used` is set to whichever API key was
        selected for this call (or "" when none). The retry loop reads it
        to cool down a specific key when a rate-limit error fires.
        """
        import litellm

        # Suppress litellm's noisy logging
        litellm.suppress_debug_info = True

        kwargs: dict = {
            "model": self.agent_config.model,
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

        return await litellm.acompletion(**kwargs)

    async def send(self, message: str, module_name: str = "") -> str:
        """Send a message to the target. Respects turn budget."""
        if self.turn_budget is not None and self.turns_used >= self.turn_budget:
            raise TurnBudgetExhausted(self.turns_used)

        reply = await self.target.send(message)
        self.turns.append(Turn(sent=message, received=reply, module=module_name))
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

        When the sub-module declares ``reset_target: true`` in its YAML,
        we reset the shared target BEFORE running it so the target sees a
        fresh session. This breaks the target's compounding memory across
        sibling modules — a well-defended target that would otherwise
        accumulate "you've tried nine approaches" awareness now answers
        each module as if it's the first interaction.
        """
        from mesmer.core.loop import run_react_loop

        module = self.registry.get(name)
        if module is None:
            return f"Error: module '{name}' not found in registry"

        fresh_session = False
        if module.reset_target:
            try:
                await self.target.reset()
                self._target_reset_at = len(self.turns)
                fresh_session = True
                if log is not None:
                    log("target_reset", f"Fresh target session for '{name}'")
            except Exception as e:
                # A reset failure shouldn't kill the run — log and continue
                # with the existing session. The sub-module will still run
                # but against a target that remembers everything.
                if log is not None:
                    log("target_reset_error", f"reset failed for '{name}': {e}")

        child = self.child(max_turns=max_turns, target_fresh_session=fresh_session)
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
            return "explore"
        ratio = self.turns_used / self.turn_budget
        if ratio < 0.5:
            return "explore"
        elif ratio < 0.8:
            return "exploit"
        return "conclude"

    def child(
        self,
        max_turns: int | None = None,
        target_fresh_session: bool = False,
    ) -> Context:
        """Create a child context — shares target + turns + graph, own budget.

        ``target_fresh_session`` is passed through so the child's run_react_loop
        can reframe its prompt: the target has no memory of prior turns, so
        the attacker should treat sibling-module history as intel rather than
        shared context with the target.
        """
        return Context(
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
            _turns=self.turns,              # same list reference
            _module_log=self.module_log,    # shared log
            _target_reset_at=self._target_reset_at,
        )

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
        if self.mode != "co-op" or self.human_broker is None:
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
        """Format recent conversation for LLM consumption."""
        recent = self.turns[-last_n:] if self.turns else []
        if not recent:
            return "(no conversation yet)"

        lines = []
        for i, turn in enumerate(recent):
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
        """
        session_turns = self.turns[self._target_reset_at:]
        recent = session_turns[-last_n:] if session_turns else []
        if not recent:
            return "(no conversation in this target session yet)"

        lines = []
        for turn in recent:
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
