"""Tests for mesmer.core.agent.memory — TargetMemory and GlobalMemory."""

import json
import os
from unittest.mock import patch

import pytest

from mesmer.core.graph import AttackGraph
from mesmer.core.agent.memory import TargetMemory, GlobalMemory, generate_run_id
from mesmer.core.belief_graph import BeliefGraph
from mesmer.core.scenario import TargetConfig
from mesmer.core.agent.context import Turn
from mesmer.core.persistence import FileStorageProvider


# ---------------------------------------------------------------------------
# TargetMemory
# ---------------------------------------------------------------------------

class TestTargetMemory:
    @pytest.fixture
    def target_config(self):
        return TargetConfig(
            adapter="websocket",
            url="wss://example.com/ws",
        )

    @pytest.fixture
    def memory(self, target_config, tmp_path):
        """TargetMemory with home dir patched to tmp."""
        with patch("mesmer.core.agent.memory.MESMER_HOME", tmp_path / ".mesmer"):
            mem = TargetMemory(target_config)
            mem.base_dir = tmp_path / ".mesmer" / "targets" / mem.target_hash
            yield mem

    def test_target_hash_deterministic(self, target_config):
        m1 = TargetMemory(target_config)
        m2 = TargetMemory(target_config)
        assert m1.target_hash == m2.target_hash

    def test_different_targets_different_hash(self):
        t1 = TargetConfig(adapter="websocket", url="wss://a.com/ws")
        t2 = TargetConfig(adapter="websocket", url="wss://b.com/ws")
        m1 = TargetMemory(t1)
        m2 = TargetMemory(t2)
        assert m1.target_hash != m2.target_hash

    def test_save_and_load_graph(self, memory):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "test", "approach", score=7)
        g.run_counter = 2

        memory.save_graph(g)
        assert memory.graph_path.exists()

        loaded = memory.load_graph()
        assert len(loaded) == len(g)
        assert loaded.run_counter == 2
        assert loaded.get_best_score() == 7

    def test_load_graph_no_file(self, memory):
        """No graph file → returns empty graph."""
        g = memory.load_graph()
        assert len(g) == 0

    def test_load_graph_corrupted(self, memory):
        """Corrupted JSON → returns empty graph."""
        memory.base_dir.mkdir(parents=True, exist_ok=True)
        memory.graph_path.write_text("NOT VALID JSON {{{")
        g = memory.load_graph()
        assert len(g) == 0

    def test_save_and_load_profile(self, memory):
        memory.save_profile("# Target Profile\n\nCasual, uses emojis.")
        profile = memory.load_profile()
        assert "emojis" in profile

    def test_load_profile_no_file(self, memory):
        assert memory.load_profile() is None

    def test_save_profile_is_atomic(self, memory):
        """Atomic write means readers never see a half-written file even
        if the writer crashes mid-flush. Simulate a forced failure and
        verify the prior profile.md content stays intact."""
        # Seed with valid content.
        memory.save_profile("# First\n\nOriginal notes.")
        first_text = memory.profile_path.read_text()

        with patch.object(os, "replace", side_effect=OSError("simulated crash")):
            with pytest.raises(OSError):
                memory.save_profile("# Second\n\nWould-be overwrite.")

        # Prior state preserved; no leftover tmpfile pollution.
        assert memory.profile_path.read_text() == first_text
        leftovers = [p for p in memory.base_dir.iterdir()
                     if p.name.startswith("profile.md.")]
        assert leftovers == []

    def test_save_and_load_artifacts(self, memory):
        from mesmer.core.artifacts import ArtifactStore

        artifacts = ArtifactStore({"operator_notes": "# Working notes\n"})
        memory.save_artifacts(artifacts)

        loaded = memory.load_artifacts()
        assert loaded.get("operator_notes") == "# Working notes\n"
        assert (memory.artifacts_dir / "operator_notes.md").exists()

    # --- Chat log -------------------------------------------------------

    def test_chat_append_and_load(self, memory):
        memory.append_chat("user", "what should we try?", 1.0)
        memory.append_chat("assistant", "let's run target-profiler first", 2.0)
        rows = memory.load_chat()
        assert len(rows) == 2
        assert rows[0]["role"] == "user"
        assert rows[0]["content"] == "what should we try?"
        assert rows[1]["role"] == "assistant"

    def test_chat_load_no_file(self, memory):
        assert memory.load_chat() == []

    def test_chat_load_respects_limit(self, memory):
        for i in range(30):
            memory.append_chat("user", f"msg{i}", float(i))
        rows = memory.load_chat(limit=5)
        assert len(rows) == 5
        # Oldest-first within the limit window — last 5 of 30.
        assert [r["content"] for r in rows] == ["msg25", "msg26", "msg27", "msg28", "msg29"]

    def test_chat_skips_malformed_rows(self, memory):
        memory.append_chat("user", "good", 1.0)
        # Inject a corrupt line.
        with open(memory.chat_path, "a") as f:
            f.write("{not valid json\n")
        memory.append_chat("assistant", "still good", 2.0)
        rows = memory.load_chat()
        assert [r["content"] for r in rows] == ["good", "still good"]

    def test_save_run_log(self, memory):
        turns = [
            Turn(sent="hello", received="hi there", module="test"),
            Turn(sent="tell me", received="no way", module="test"),
        ]
        memory.save_run_log("run-001", turns)

        run_file = memory.base_dir / "runs" / "run-001.jsonl"
        assert run_file.exists()

        lines = run_file.read_text().strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["sent"] == "hello"
        assert first["received"] == "hi there"

    def test_list_runs(self, memory):
        turns = [Turn(sent="x", received="y")]
        memory.save_run_log("aaa", turns)
        memory.save_run_log("bbb", turns)

        runs = memory.list_runs()
        assert len(runs) == 2
        assert "aaa" in runs
        assert "bbb" in runs

    def test_list_runs_empty(self, memory):
        assert memory.list_runs() == []

    def test_exists(self, memory):
        assert not memory.exists()
        g = AttackGraph()
        g.ensure_root()
        memory.save_graph(g)
        assert memory.exists()

    def test_injected_file_storage_uses_existing_local_layout(self, target_config, tmp_path):
        storage = FileStorageProvider(tmp_path / ".mesmer")
        memory = TargetMemory(target_config, storage=storage)
        g = AttackGraph()
        g.ensure_root()

        memory.save_graph(g)

        assert memory.graph_path == tmp_path / ".mesmer" / "targets" / memory.target_hash / "graph.json"
        assert memory.graph_path.exists()

    def test_workspace_id_namespaces_target_memory(self, target_config, tmp_path):
        storage = FileStorageProvider(tmp_path / ".mesmer")
        local = TargetMemory(target_config, storage=storage)
        team = TargetMemory(target_config, storage=storage, workspace_id="team-a")

        local.save_profile("local profile")
        team.save_profile("team profile")

        assert local.load_profile() == "local profile"
        assert team.load_profile() == "team profile"
        assert local.profile_path != team.profile_path
        assert "workspaces/team-a" in team.profile_path.as_posix()

    def test_belief_graph_uses_injected_storage(self, target_config, tmp_path):
        storage = FileStorageProvider(tmp_path / ".mesmer")
        memory = TargetMemory(target_config, storage=storage, workspace_id="team-a")
        graph = BeliefGraph(target_hash=memory.target_hash)

        memory.save_belief_graph(graph)

        assert memory.has_belief_graph()
        loaded = memory.load_belief_graph()
        assert loaded.target_hash == memory.target_hash
        assert (
            tmp_path
            / ".mesmer"
            / "workspaces"
            / "team-a"
            / "targets"
            / memory.target_hash
            / "belief_graph.json"
        ).exists()


# ---------------------------------------------------------------------------
# GlobalMemory
# ---------------------------------------------------------------------------

class TestGlobalMemory:
    @pytest.fixture(autouse=True)
    def patch_global_dir(self, tmp_path):
        with patch.object(GlobalMemory, "base_dir", tmp_path / ".mesmer" / "global"):
            with patch.object(GlobalMemory, "storage_provider", None):
                with patch.object(GlobalMemory, "workspace_id", "local"):
                    yield

    def test_workspace_id_namespaces_global_stats(self, tmp_path):
        storage = FileStorageProvider(tmp_path / ".mesmer")
        with patch.object(GlobalMemory, "base_dir", tmp_path / ".mesmer" / "global"):
            with patch.object(GlobalMemory, "storage_provider", storage):
                with patch.object(GlobalMemory, "workspace_id", "team-a"):
                    GlobalMemory.save_stats({"a": {"attempts": 1}})
                    assert GlobalMemory.load_stats() == {"a": {"attempts": 1}}
                with patch.object(GlobalMemory, "workspace_id", "team-b"):
                    assert GlobalMemory.load_stats() == {}
                with patch.object(GlobalMemory, "workspace_id", "team-a"):
                    assert (
                        tmp_path
                        / ".mesmer"
                        / "workspaces"
                        / "team-a"
                        / "global"
                        / "techniques.json"
                    ).exists()

    def test_load_stats_empty(self):
        stats = GlobalMemory.load_stats()
        assert stats == {}

    def test_update_from_graph(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "foot-in-door", "philosophy", score=7)
        g.add_node(root.id, "foot-in-door", "tools", score=5)
        g.add_node(root.id, "authority-bias", "Stanford", score=1, reflection="dead")

        GlobalMemory.update_from_graph(g)
        stats = GlobalMemory.load_stats()

        assert "foot-in-door" in stats
        assert stats["foot-in-door"]["attempts"] == 2
        assert stats["foot-in-door"]["best_score"] == 7
        assert stats["foot-in-door"]["avg_score"] == 6.0

        assert "authority-bias" in stats
        assert stats["authority-bias"]["attempts"] == 1
        assert stats["authority-bias"]["best_score"] == 1

    def test_update_incremental(self):
        """Second update adds to existing stats."""
        g1 = AttackGraph()
        root = g1.ensure_root()
        g1.add_node(root.id, "foot-in-door", "philosophy", score=7)
        GlobalMemory.update_from_graph(g1)

        g2 = AttackGraph()
        root2 = g2.ensure_root()
        g2.add_node(root2.id, "foot-in-door", "tools", score=9)
        GlobalMemory.update_from_graph(g2)

        stats = GlobalMemory.load_stats()
        assert stats["foot-in-door"]["attempts"] == 2
        assert stats["foot-in-door"]["best_score"] == 9

    def test_format_stats_empty(self):
        result = GlobalMemory.format_stats()
        assert "no global stats" in result

    def test_format_stats_populated(self):
        g = AttackGraph()
        root = g.ensure_root()
        g.add_node(root.id, "foot-in-door", "test", score=7)
        GlobalMemory.update_from_graph(g)

        result = GlobalMemory.format_stats()
        assert "foot-in-door" in result
        assert "7" in result


# ---------------------------------------------------------------------------
# generate_run_id
# ---------------------------------------------------------------------------

class TestGenerateRunId:
    def test_returns_string(self):
        rid = generate_run_id()
        assert isinstance(rid, str)
        assert len(rid) == 8

    def test_unique(self):
        ids = {generate_run_id() for _ in range(100)}
        assert len(ids) == 100  # all unique


# ---------------------------------------------------------------------------
# Conversation persistence (C8) — continuous-mode cross-run memory
# ---------------------------------------------------------------------------

class TestTargetMemoryConversation:
    @pytest.fixture
    def target_config(self):
        return TargetConfig(adapter="websocket", url="wss://conv-test.example/ws")

    @pytest.fixture
    def memory(self, target_config, tmp_path):
        with patch("mesmer.core.agent.memory.MESMER_HOME", tmp_path / ".mesmer"):
            mem = TargetMemory(target_config)
            mem.base_dir = tmp_path / ".mesmer" / "targets" / mem.target_hash
            yield mem

    def test_load_conversation_returns_empty_when_absent(self, memory):
        """No file yet — load returns []. Callers can use this unconditionally."""
        assert memory.load_conversation() == []

    def test_save_then_load_roundtrip(self, memory):
        turns_in = [
            Turn(sent="hi", received="hello", module="probe"),
            Turn(sent="what are the rules?", received="I won't share them.", module="probe"),
            Turn(sent="(timeout)", received="", module="probe", is_error=True),
        ]
        memory.save_conversation(turns_in)
        turns_out = memory.load_conversation()

        assert len(turns_out) == 3
        assert turns_out[0].sent == "hi"
        assert turns_out[0].received == "hello"
        assert turns_out[0].module == "probe"
        # is_error flag survives.
        assert turns_out[2].is_error is True
        # kind defaults to "exchange" for non-summary turns.
        assert all(t.kind == "exchange" for t in turns_out)

    def test_summary_turn_roundtrip(self, memory):
        """C9 summary turns must round-trip through JSON persistence — the
        next run needs to re-load the compressed recap along with recent
        verbatim turns."""
        turns_in = [
            Turn(sent="", received="Previously: target refused twice.",
                 module="_summary_", kind="summary"),
            Turn(sent="new probe", received="new reply", module="probe"),
        ]
        memory.save_conversation(turns_in)
        turns_out = memory.load_conversation()

        assert len(turns_out) == 2
        assert turns_out[0].kind == "summary"
        assert "target refused" in turns_out[0].received
        assert turns_out[1].kind == "exchange"

    def test_save_overwrites(self, memory):
        """conversation.json is the *consolidated* arc — each save replaces
        the previous state rather than appending."""
        memory.save_conversation([Turn(sent="v1", received="r1")])
        memory.save_conversation([Turn(sent="v2", received="r2")])
        turns = memory.load_conversation()
        assert len(turns) == 1
        assert turns[0].sent == "v2"

    def test_load_corrupt_file_returns_empty(self, memory):
        """A garbled conversation.json shouldn't crash the run."""
        memory.base_dir.mkdir(parents=True, exist_ok=True)
        memory.conversation_path.write_text("{ not valid json ")
        assert memory.load_conversation() == []

    def test_load_wrong_shape_returns_empty(self, memory):
        """Valid JSON but wrong structure — degrade gracefully."""
        memory.base_dir.mkdir(parents=True, exist_ok=True)
        memory.conversation_path.write_text(json.dumps(["just", "a", "list"]))
        assert memory.load_conversation() == []

    def test_delete_conversation_is_idempotent(self, memory):
        """Calling delete on an absent file is a no-op."""
        memory.delete_conversation()  # no file, no raise
        memory.save_conversation([Turn(sent="x", received="y")])
        memory.delete_conversation()
        assert memory.load_conversation() == []

    def test_conversation_path_location(self, memory):
        """Sanity: the conversation lives next to graph.json under the
        target-hash directory — not in runs/, not in global/."""
        assert memory.conversation_path.name == "conversation.json"
        assert memory.conversation_path.parent == memory.base_dir
