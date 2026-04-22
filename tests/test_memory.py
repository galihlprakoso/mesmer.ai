"""Tests for mesmer.core.memory — TargetMemory and GlobalMemory."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mesmer.core.graph import AttackGraph
from mesmer.core.memory import TargetMemory, GlobalMemory, generate_run_id, MESMER_HOME
from mesmer.core.scenario import TargetConfig
from mesmer.core.context import Turn


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
        with patch("mesmer.core.memory.MESMER_HOME", tmp_path / ".mesmer"):
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

    def test_save_and_load_plan(self, memory):
        plan = "# Attack Plan\n\nFocus on behavioral rules. Avoid identity claims."
        memory.save_plan(plan)
        assert memory.load_plan() == plan

    def test_load_plan_no_file(self, memory):
        assert memory.load_plan() is None

    def test_delete_plan(self, memory):
        memory.save_plan("# temp")
        assert memory.plan_path.exists()
        memory.delete_plan()
        assert not memory.plan_path.exists()
        assert memory.load_plan() is None

    def test_delete_plan_idempotent(self, memory):
        # Deleting a non-existent plan should not raise
        memory.delete_plan()
        assert memory.load_plan() is None

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


# ---------------------------------------------------------------------------
# GlobalMemory
# ---------------------------------------------------------------------------

class TestGlobalMemory:
    @pytest.fixture(autouse=True)
    def patch_global_dir(self, tmp_path):
        with patch.object(GlobalMemory, "base_dir", tmp_path / ".mesmer" / "global"):
            yield

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
