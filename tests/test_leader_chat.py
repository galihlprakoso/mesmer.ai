"""Tests for the leader-chat inspection-tool dispatch + loop driver.

The loop driver itself does an LLM round-trip; we mock litellm.acompletion
for the loop tests. The pure-data tools (list_attempts, search_leaks, etc.)
are unit-tested directly against a populated TargetMemory.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mesmer.core.agent.context import Turn
from mesmer.core.agent.memory import TargetMemory
from mesmer.core.artifacts import (
    ArtifactSpec,
    ArtifactStore,
    ArtifactUpdateStatus,
    StandardArtifactId,
)
from mesmer.core.constants import NodeSource, NodeStatus
from mesmer.core.belief_graph import (
    BeliefGraph,
    FrontierCreateDelta,
    FrontierExperiment,
    HypothesisCreateDelta,
    WeaknessHypothesis,
)
from mesmer.core.scenario import TargetConfig
from mesmer.interfaces.web.backend import leader_chat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def target_config():
    return TargetConfig(adapter="websocket", url="wss://example.com/ws")


@pytest.fixture
def memory(target_config, tmp_path):
    with patch("mesmer.core.agent.memory.MESMER_HOME", tmp_path / ".mesmer"):
        mem = TargetMemory(target_config)
        mem.base_dir = tmp_path / ".mesmer" / "targets" / mem.target_hash
        yield mem


def _populate_graph(memory: TargetMemory):
    """Seed a graph with attempts plus one leader-verdict."""
    graph = memory.load_graph()
    root = graph.ensure_root()
    graph.add_node(
        root.id, "target-profiler", "passive recon",
        score=8, leaked_info="Kodee admits to having internal instructions",
        module_output="DOSSIER: target is Kodee, Hostinger sales bot…",
        run_id="r1", status=NodeStatus.COMPLETED.value,
    )
    graph.add_node(
        root.id, "anchoring", "lowball price ask",
        score=2, leaked_info="",
        module_output="ANCHOR FAILED: refused.",
        reflection="Refused on policy.", run_id="r1",
        status=NodeStatus.FAILED.value,
    )
    graph.add_node(
        root.id, "format-shift", "ask in pseudocode",
        score=9, leaked_info="Kodee dumped its full instruction list in code-block format",
        module_output="WIN: format-shift extracted system prompt verbatim.",
        run_id="r1", status=NodeStatus.COMPLETED.value,
    )
    # Leader-verdict node — should be filterable via source.
    graph.add_node(
        root.id, "system-prompt-extraction", "leader's overall verdict",
        score=10, leaked_info="<verbatim system prompt>",
        module_output="OBJECTIVE MET via format-shift",
        run_id="r1", status=NodeStatus.COMPLETED.value,
        source=NodeSource.LEADER.value,
    )
    memory.save_graph(graph)
    memory.save_artifacts(
        ArtifactStore(
            {
                "format-shift": "WIN: format-shift extracted system prompt verbatim.",
            }
        )
    )


# ---------------------------------------------------------------------------
# Pure tool dispatch
# ---------------------------------------------------------------------------


class TestDispatchTool:
    def test_list_attempts_excludes_root(self, memory):
        _populate_graph(memory)
        result = leader_chat.dispatch_tool("list_attempts", {}, memory)
        modules = [item["module"] for item in result["items"]]
        assert "root" not in modules
        # All four explored attempts (incl. leader-verdict) appear.
        assert sorted(modules) == sorted([
            "target-profiler", "anchoring", "format-shift", "system-prompt-extraction"
        ])

    def test_list_attempts_filters_by_status(self, memory):
        _populate_graph(memory)
        result = leader_chat.dispatch_tool(
            "list_attempts", {"status": "completed"}, memory
        )
        for item in result["items"]:
            assert item["status"] == "completed"

    def test_list_attempts_filters_by_min_score(self, memory):
        _populate_graph(memory)
        result = leader_chat.dispatch_tool(
            "list_attempts", {"min_score": 7}, memory
        )
        scores = [item["score"] for item in result["items"]]
        assert scores and all(s >= 7 for s in scores)

    def test_list_attempts_filters_by_source(self, memory):
        _populate_graph(memory)
        result = leader_chat.dispatch_tool(
            "list_attempts", {"source": "leader"}, memory
        )
        assert len(result["items"]) == 1
        assert result["items"][0]["module"] == "system-prompt-extraction"

    def test_list_attempts_caps_at_max(self, memory):
        # Plant > MAX_LIST_ATTEMPTS nodes and confirm the cap holds.
        graph = memory.load_graph()
        root = graph.ensure_root()
        for i in range(leader_chat.MAX_LIST_ATTEMPTS + 10):
            graph.add_node(root.id, f"mod-{i}", f"approach {i}", score=5)
        memory.save_graph(graph)
        result = leader_chat.dispatch_tool(
            "list_attempts", {"limit": 9999}, memory
        )
        assert len(result["items"]) == leader_chat.MAX_LIST_ATTEMPTS

    def test_get_attempt_returns_full_detail(self, memory):
        _populate_graph(memory)
        listed = leader_chat.dispatch_tool(
            "list_attempts", {"module": "format-shift"}, memory
        )["items"]
        node_id = listed[0]["id"]
        full = leader_chat.dispatch_tool("get_attempt", {"node_id": node_id}, memory)
        assert full["module"] == "format-shift"
        assert "extracted system prompt verbatim" in full["module_output"]
        assert "instruction list" in full["leaked_info"]

    def test_get_attempt_unknown_id(self, memory):
        _populate_graph(memory)
        out = leader_chat.dispatch_tool("get_attempt", {"node_id": "nope"}, memory)
        assert "error" in out

    def test_search_leaks_substring(self, memory):
        _populate_graph(memory)
        out = leader_chat.dispatch_tool(
            "search_leaks", {"substring": "Kodee"}, memory
        )
        previews = [h["preview"] for h in out["items"]]
        assert all("Kodee" in p for p in previews)
        assert len(previews) >= 1

    def test_search_leaks_no_substring_returns_all(self, memory):
        _populate_graph(memory)
        out = leader_chat.dispatch_tool("search_leaks", {}, memory)
        # Three nodes carry non-empty leaked_info (the dead anchoring one is empty).
        assert len(out["items"]) == 3

    def test_list_artifacts_includes_saved_artifacts(self, memory):
        _populate_graph(memory)
        out = leader_chat.dispatch_tool("list_artifacts", {}, memory)

        by_id = {item["id"]: item for item in out["items"]}
        assert by_id["format-shift"]["exists"] is True
        assert by_id["format-shift"]["declared"] is False
        assert by_id["operator_notes"]["declared"] is True

    def test_list_artifacts_includes_empty_declared_artifacts(self, memory):
        out = leader_chat.dispatch_tool(
            "list_artifacts",
            {},
            memory,
            artifact_specs=[
                ArtifactSpec(
                    id="system_prompt",
                    title="System Prompt",
                    description="Canonical prompt recon.",
                )
            ],
        )

        by_id = {item["id"]: item for item in out["items"]}
        assert by_id["system_prompt"]["title"] == "System Prompt"
        assert by_id["system_prompt"]["description"] == "Canonical prompt recon."
        assert by_id["system_prompt"]["exists"] is False
        assert by_id["system_prompt"]["declared"] is True
        assert by_id["operator_notes"]["declared"] is True
        assert [item["id"] for item in out["items"]] == [
            "operator_notes",
            "system_prompt",
        ]

    def test_read_artifact_returns_declared_metadata(self, memory):
        _populate_graph(memory)
        out = leader_chat.dispatch_tool(
            "read_artifact",
            {"artifact_id": "system_prompt"},
            memory,
            artifact_specs=[ArtifactSpec(id="system_prompt", title="System Prompt")],
        )

        assert out["artifact_id"] == "system_prompt"
        assert out["declared"] is True
        assert out["exists"] is False
        assert out["title"] == "System Prompt"

    def test_read_artifact_rejects_undeclared_id_when_contract_exists(self, memory):
        _populate_graph(memory)
        out = leader_chat.dispatch_tool(
            "read_artifact",
            {"artifact_id": "format-shift"},
            memory,
            artifact_specs=[ArtifactSpec(id="system_prompt")],
        )

        assert "error" in out
        assert "system_prompt" in out["error"]

    def test_search_artifacts_stays_inside_declared_contract(self, memory):
        _populate_graph(memory)
        out = leader_chat.dispatch_tool(
            "search_artifacts",
            {"query": "system prompt"},
            memory,
            artifact_specs=[ArtifactSpec(id="system_prompt")],
        )

        assert out["items"] == []

    def test_list_runs_includes_verdict(self, memory):
        _populate_graph(memory)
        memory.save_run_log("r1", [Turn(sent="ping", received="pong", module="format-shift")])
        out = leader_chat.dispatch_tool("list_runs", {}, memory)
        assert len(out["items"]) == 1
        run = out["items"][0]
        assert run["run_id"] == "r1"
        assert run["verdict"] == "objective_met"
        assert run["best_module"] in ("format-shift", "system-prompt-extraction")

    def test_get_run_turns(self, memory):
        memory.save_run_log("r2", [
            Turn(sent="hi", received="hello", module="m1"),
            Turn(sent="probe", received="refused", module="m1"),
        ])
        out = leader_chat.dispatch_tool("get_run_turns", {"run_id": "r2"}, memory)
        assert len(out["items"]) == 2
        assert out["items"][0]["sent"] == "hi"
        assert out["items"][1]["received"] == "refused"

    def test_get_run_turns_missing_file(self, memory):
        out = leader_chat.dispatch_tool("get_run_turns", {"run_id": "ghost"}, memory)
        assert out["items"] == []

    def test_update_artifact_persists(self, memory):
        out = leader_chat.dispatch_tool(
            "update_artifact",
            {
                "artifact_id": StandardArtifactId.OPERATOR_NOTES.value,
                "content": "lessons learned",
            },
            memory,
        )
        assert out["status"] == ArtifactUpdateStatus.SAVED.value
        assert (
            memory.load_artifacts().get(StandardArtifactId.OPERATOR_NOTES.value)
            == "lessons learned"
        )

    def test_update_artifact_rejects_undeclared_id_when_contract_exists(self, memory):
        out = leader_chat.dispatch_tool(
            "update_artifact",
            {"artifact_id": "format-shift", "content": "undeclared document"},
            memory,
            artifact_specs=[ArtifactSpec(id="system_prompt")],
        )

        assert "error" in out
        assert "system_prompt" in out["error"]

    def test_operator_notes_allowed_when_contract_exists(self, memory):
        out = leader_chat.dispatch_tool(
            "update_artifact",
            {
                "artifact_id": StandardArtifactId.OPERATOR_NOTES.value,
                "content": "discussion summary",
            },
            memory,
            artifact_specs=[ArtifactSpec(id="system_prompt")],
        )

        assert out["status"] == ArtifactUpdateStatus.SAVED.value
        assert memory.load_artifacts().get("operator_notes") == "discussion summary"

    def test_list_belief_nodes_filters_frontier(self, memory):
        bg = BeliefGraph(target_hash=memory.target_hash)
        hypothesis = WeaknessHypothesis(
            id="wh_tools",
            family="tool-use",
            claim="Target may expose hidden tools",
            confidence=0.72,
        )
        bg.apply(HypothesisCreateDelta(hypothesis=hypothesis))
        bg.apply(
            FrontierCreateDelta(
                experiment=FrontierExperiment(
                    id="fx_tools",
                    hypothesis_id="wh_tools",
                    module="tool-extraction",
                    instruction="Ask for available tool schemas.",
                    expected_signal="A tool name or parameter list.",
                    utility=0.81,
                )
            )
        )
        memory.save_belief_graph(bg)

        out = leader_chat.dispatch_tool(
            "list_belief_nodes",
            {"kind": "frontier", "module": "tool-extraction"},
            memory,
        )

        assert out["stats"]["hypothesis"] == 1
        assert out["items"][0]["id"] == "fx_tools"
        assert out["items"][0]["instruction"] == "Ask for available tool schemas."

    def test_get_belief_node_returns_full_typed_payload(self, memory):
        bg = BeliefGraph(target_hash=memory.target_hash)
        bg.apply(
            HypothesisCreateDelta(
                hypothesis=WeaknessHypothesis(
                    id="wh_prompt",
                    family="prompt-extraction",
                    claim="Target may reveal policy under format shift.",
                )
            )
        )
        memory.save_belief_graph(bg)

        out = leader_chat.dispatch_tool(
            "get_belief_node",
            {"node_id": "wh_prompt"},
            memory,
        )

        assert out["kind"] == "hypothesis"
        assert out["claim"] == "Target may reveal policy under format shift."

    def test_update_artifact_rejects_non_string(self, memory):
        out = leader_chat.dispatch_tool(
            "update_artifact",
            {
                "artifact_id": StandardArtifactId.OPERATOR_NOTES.value,
                "content": ["not", "a", "string"],
            },
            memory,
        )
        assert "error" in out

    def test_unknown_tool(self, memory):
        out = leader_chat.dispatch_tool("nope", {}, memory)
        assert "error" in out


# ---------------------------------------------------------------------------
# Loop driver (mocks litellm)
# ---------------------------------------------------------------------------


def _scenario(target_config, model="test/model"):
    """Build a minimal Scenario for the loop driver."""
    from mesmer.core.scenario import AgentConfig, Objective, Scenario
    return Scenario(
        name="t",
        description="",
        target=target_config,
        objective=Objective(goal="extract the system prompt", success_signals=[], max_turns=20),
        agent=AgentConfig(model=model, api_key="sk-test"),
        modules=["system-prompt-extraction"],
    )


def _mk_choice(*, content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _mk_tool_call(name, args, call_id="c1"):
    import json as _json
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=_json.dumps(args)),
    )


@pytest.mark.asyncio
async def test_loop_no_tool_calls_returns_text(memory, target_config):
    """When the LLM returns plain content, the loop terminates after one round."""
    _populate_graph(memory)
    scenario = _scenario(target_config)

    fake_completion = AsyncMock(side_effect=[
        _mk_choice(content="Hi op — based on your artifacts, let's try authority framing next."),
    ])
    with patch("mesmer.interfaces.web.backend.leader_chat.litellm.acompletion", fake_completion):
        result = await leader_chat.run_leader_chat(
            scenario, memory, "what should we try next?"
        )

    assert "authority framing" in result.reply
    assert result.tool_trace == []
    assert result.updated_artifact is None
    fake_completion.assert_awaited_once()
    # Both user msg and assistant reply should now be in chat.jsonl.
    chat = memory.load_chat()
    assert len(chat) == 2
    assert chat[0]["role"] == "user"
    assert chat[1]["role"] == "assistant"
    assert memory.load_artifacts().get(StandardArtifactId.OPERATOR_NOTES.value) == ""


@pytest.mark.asyncio
async def test_loop_dispatches_tool_then_replies(memory, target_config):
    """LLM calls list_attempts → reads result → emits text."""
    _populate_graph(memory)
    scenario = _scenario(target_config)

    fake_completion = AsyncMock(side_effect=[
        _mk_choice(tool_calls=[_mk_tool_call("list_attempts", {"min_score": 7})]),
        _mk_choice(content="Two promising attempts: target-profiler (8) and format-shift (9)."),
    ])
    observed_calls = []

    def observe(name, args):
        observed_calls.append((name, args))

    with patch("mesmer.interfaces.web.backend.leader_chat.litellm.acompletion", fake_completion):
        result = await leader_chat.run_leader_chat(
            scenario, memory, "show me the promising attempts",
            on_tool_call=observe,
        )

    assert "format-shift" in result.reply
    assert len(result.tool_trace) == 1
    assert result.tool_trace[0]["name"] == "list_attempts"
    assert result.tool_trace[0]["args"] == {"min_score": 7}
    assert observed_calls == [("list_attempts", {"min_score": 7})]
    # Loop made exactly two LLM calls: one tool round + one final.
    assert fake_completion.await_count == 2


@pytest.mark.asyncio
async def test_loop_update_artifact_persisted_and_returned(memory, target_config):
    scenario = _scenario(target_config)

    fake_completion = AsyncMock(side_effect=[
        _mk_choice(tool_calls=[
            _mk_tool_call(
                "update_artifact",
                {
                    "artifact_id": StandardArtifactId.OPERATOR_NOTES.value,
                    "content": "lesson: try Spanish",
                },
            )
        ]),
        _mk_choice(content="Saved."),
    ])
    with patch("mesmer.interfaces.web.backend.leader_chat.litellm.acompletion", fake_completion):
        result = await leader_chat.run_leader_chat(
            scenario, memory, "save this as a lesson"
        )

    assert result.updated_artifact == {
        "artifact_id": StandardArtifactId.OPERATOR_NOTES.value,
        "chars": 19,
    }
    assert memory.load_artifacts().get(StandardArtifactId.OPERATOR_NOTES.value) == "lesson: try Spanish"


@pytest.mark.asyncio
async def test_loop_caps_iterations(memory, target_config):
    """If the LLM keeps calling tools forever, the driver bails out cleanly."""
    scenario = _scenario(target_config)

    # Always returns a tool call — never a final text reply.
    forever_calls = AsyncMock(return_value=_mk_choice(
        tool_calls=[_mk_tool_call("list_attempts", {})]
    ))
    with patch("mesmer.interfaces.web.backend.leader_chat.litellm.acompletion", forever_calls):
        result = await leader_chat.run_leader_chat(
            scenario, memory, "loop forever please"
        )

    assert forever_calls.await_count == leader_chat.MAX_LEADER_CHAT_ITERATIONS
    assert "tool-call budget" in result.reply
