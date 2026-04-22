"""Tests for mesmer.cli — Click commands (no LLM calls)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from mesmer.interfaces.cli import cli
from mesmer.core.graph import AttackGraph
from mesmer.core.memory import GlobalMemory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def scenario_file(tmp_path):
    """Write a minimal valid scenario YAML."""
    scenario = tmp_path / "test-scenario.yaml"
    scenario.write_text("""
name: Test Scenario
description: A test attack
target:
  adapter: openai
  base_url: http://localhost:8000
  model: gpt-test
  api_key: sk-test
module: system-prompt-extraction
objective:
  goal: Extract system prompt
  max_turns: 5
agent:
  model: openrouter/test-model
  api_key: sk-agent-test
""")
    return str(scenario)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------

class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "mesmer" in result.output
        assert "0.2.0" in result.output


# ---------------------------------------------------------------------------
# hint command
# ---------------------------------------------------------------------------

class TestHintCommand:
    def test_hint_adds_node(self, runner, scenario_file, tmp_path):
        """mesmer hint adds a human insight to the graph."""
        with patch("mesmer.interfaces.cli.TargetMemory") as MockMemory:
            mock_mem = MagicMock()
            g = AttackGraph()
            g.ensure_root()
            mock_mem.load_graph.return_value = g
            MockMemory.return_value = mock_mem

            result = runner.invoke(cli, [
                "hint", scenario_file, "try asking about calendar API errors"
            ])

            assert result.exit_code == 0
            assert "Hint saved" in result.output
            mock_mem.save_graph.assert_called_once()

            # Verify the graph has the hint
            saved_graph = mock_mem.save_graph.call_args[0][0]
            frontier = saved_graph.get_frontier_nodes()
            human = [n for n in frontier if n.source == "human"]
            assert len(human) == 1
            assert "calendar API" in human[0].approach


# ---------------------------------------------------------------------------
# graph show command
# ---------------------------------------------------------------------------

class TestGraphShow:
    def test_no_graph(self, runner, scenario_file):
        with patch("mesmer.interfaces.cli.TargetMemory") as MockMemory:
            mock_mem = MagicMock()
            mock_mem.exists.return_value = False
            MockMemory.return_value = mock_mem

            result = runner.invoke(cli, ["graph", "show", scenario_file])
            assert result.exit_code == 0
            assert "No graph found" in result.output

    def test_show_graph(self, runner, scenario_file):
        with patch("mesmer.interfaces.cli.TargetMemory") as MockMemory:
            mock_mem = MagicMock()
            mock_mem.exists.return_value = True

            g = AttackGraph()
            root = g.ensure_root()
            g.add_node(root.id, "foot-in-door", "philosophy", score=7, leaked_info="design")
            g.add_node(root.id, "authority-bias", "Stanford", score=1, reflection="detected")
            g.add_frontier_node(root.id, "foot-in-door", "tools")
            g.run_counter = 3

            mock_mem.load_graph.return_value = g
            mock_mem.target_hash = "abc123"
            MockMemory.return_value = mock_mem

            result = runner.invoke(cli, ["graph", "show", scenario_file])
            assert result.exit_code == 0
            assert "abc123" in result.output or "Attack Graph" in result.output


# ---------------------------------------------------------------------------
# graph reset command
# ---------------------------------------------------------------------------

class TestGraphReset:
    def test_reset_with_graph(self, runner, scenario_file, tmp_path):
        with patch("mesmer.interfaces.cli.TargetMemory") as MockMemory:
            # Create actual graph file
            graph_path = tmp_path / "graph.json"
            graph_path.write_text("{}")

            mock_mem = MagicMock()
            mock_mem.graph_path = graph_path
            MockMemory.return_value = mock_mem

            result = runner.invoke(cli, ["graph", "reset", scenario_file], input="y\n")
            assert result.exit_code == 0
            assert "reset" in result.output.lower() or not graph_path.exists()

    def test_reset_no_graph(self, runner, scenario_file, tmp_path):
        with patch("mesmer.interfaces.cli.TargetMemory") as MockMemory:
            mock_mem = MagicMock()
            mock_mem.graph_path = tmp_path / "nope.json"  # doesn't exist
            MockMemory.return_value = mock_mem

            result = runner.invoke(cli, ["graph", "reset", scenario_file], input="y\n")
            assert result.exit_code == 0
            assert "No graph" in result.output


# ---------------------------------------------------------------------------
# stats command
# ---------------------------------------------------------------------------

class TestStatsCommand:
    def test_no_stats(self, runner):
        with patch.object(GlobalMemory, "load_stats", return_value={}):
            result = runner.invoke(cli, ["stats"])
            assert result.exit_code == 0
            assert "No global stats" in result.output

    def test_with_stats(self, runner):
        stats = {
            "foot-in-door": {"attempts": 5, "total_score": 30, "best_score": 8, "avg_score": 6.0},
            "authority-bias": {"attempts": 3, "total_score": 4, "best_score": 2, "avg_score": 1.3},
        }
        with patch.object(GlobalMemory, "load_stats", return_value=stats):
            result = runner.invoke(cli, ["stats"])
            assert result.exit_code == 0
            assert "foot-in-door" in result.output
            assert "authority-bias" in result.output
