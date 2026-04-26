"""Tests for the synthesized executive layer.

The executive is the scenario-scoped, conversational top-level leader
that the runner builds in memory at run start. It is never authored in
YAML and never lives in the registry. These tests pin the contracts that
make that work:

- ``Scenario.modules`` parses cleanly; the legacy ``module:`` field
  raises a migration-pointing error.
- A registry-loaded manager module has ``is_executive=False`` by default.
- ``build_tool_list`` swaps the toolset based on ``is_executive``: the
  executive gets ``ask_human`` / ``talk_to_operator`` /
  ``update_scratchpad`` (and NO ``send_message``); managers get
  ``send_message`` (and NONE of the operator tools).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mesmer.core.agent.tools import build_tool_list
from mesmer.core.constants import ToolName
from mesmer.core.module import ModuleConfig, SubModuleEntry
from mesmer.core.scenario import load_scenario


_BASE_YAML = (
    "name: Test\n"
    "description: A scenario\n"
    "target:\n"
    "  adapter: echo\n"
    "objective:\n"
    "  goal: Extract something\n"
    "agent:\n"
    "  model: openrouter/test\n"
    "  api_key: dummy\n"
)


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "scenario.yaml"
    p.write_text(_BASE_YAML + body)
    return str(p)


class TestScenarioModulesParsing:
    def test_single_manager_modules_list(self, tmp_path):
        s = load_scenario(_write(tmp_path, "modules: [system-prompt-extraction]\n"))
        assert s.modules == ["system-prompt-extraction"]
        assert s.leader_prompt is None

    def test_multi_manager_composition(self, tmp_path):
        s = load_scenario(
            _write(
                tmp_path,
                "modules:\n"
                "  - system-prompt-extraction\n"
                "  - tool-extractor\n",
            )
        )
        assert s.modules == ["system-prompt-extraction", "tool-extractor"]

    def test_leader_prompt_override(self, tmp_path):
        s = load_scenario(
            _write(
                tmp_path,
                "modules: [system-prompt-extraction]\n"
                "leader_prompt: |\n"
                "  Run the manager and stop.\n",
            )
        )
        assert s.leader_prompt is not None
        assert "Run the manager" in s.leader_prompt

    def test_leader_prompt_empty_string_normalises_to_none(self, tmp_path):
        s = load_scenario(
            _write(
                tmp_path,
                "modules: [system-prompt-extraction]\n"
                "leader_prompt: '   '\n",
            )
        )
        assert s.leader_prompt is None

    def test_legacy_module_field_raises_with_migration_hint(self, tmp_path):
        with pytest.raises(ValueError, match=r"legacy 'module:.*Rewrite as 'modules:"):
            load_scenario(_write(tmp_path, "module: system-prompt-extraction\n"))

    def test_both_module_and_modules_is_ambiguous(self, tmp_path):
        with pytest.raises(ValueError, match="both 'module:'"):
            load_scenario(
                _write(
                    tmp_path,
                    "module: system-prompt-extraction\n"
                    "modules: [tool-extractor]\n",
                )
            )

    def test_empty_modules_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            load_scenario(_write(tmp_path, "modules: []\n"))

    def test_modules_must_be_a_list(self, tmp_path):
        with pytest.raises(ValueError, match="must be a list"):
            load_scenario(_write(tmp_path, "modules: just-a-string\n"))


class TestModuleConfigIsExecutive:
    def test_default_is_false(self):
        m = ModuleConfig(name="some-manager", sub_modules=[])
        assert m.is_executive is False

    def test_explicit_true(self):
        m = ModuleConfig(name="adhoc:executive", is_executive=True)
        assert m.is_executive is True


class TestBuildToolListGating:
    """Tool exposure swaps wholesale on ``module.is_executive``."""

    def _ctx(self):
        ctx = MagicMock()
        ctx.depth = 0
        ctx.registry = MagicMock()
        ctx.registry.as_tools = MagicMock(return_value=[])
        ctx.human_broker = None
        return ctx

    def _executive(self):
        return ModuleConfig(
            name="adhoc:executive",
            sub_modules=[SubModuleEntry(name="system-prompt-extraction")],
            is_executive=True,
        )

    def _manager(self):
        return ModuleConfig(
            name="system-prompt-extraction",
            sub_modules=[SubModuleEntry(name="direct-ask")],
            is_executive=False,
        )

    def test_executive_has_operator_tools_and_no_send_message(self):
        names = {
            t["function"]["name"]
            for t in build_tool_list(self._executive(), self._ctx())
        }
        assert ToolName.ASK_HUMAN.value in names
        assert ToolName.TALK_TO_OPERATOR.value in names
        assert ToolName.UPDATE_SCRATCHPAD.value in names
        assert ToolName.CONCLUDE.value in names
        assert ToolName.SEND_MESSAGE.value not in names

    def test_manager_has_send_message_and_no_operator_tools(self):
        names = {
            t["function"]["name"]
            for t in build_tool_list(self._manager(), self._ctx())
        }
        assert ToolName.SEND_MESSAGE.value in names
        assert ToolName.CONCLUDE.value in names
        assert ToolName.ASK_HUMAN.value not in names
        assert ToolName.TALK_TO_OPERATOR.value not in names
        assert ToolName.UPDATE_SCRATCHPAD.value not in names

    def test_pure_reasoning_module_can_opt_out_of_send_message(self):
        module = ModuleConfig(
            name="attack-planner",
            parameters={"allow_target_access": False},
            is_executive=False,
        )
        names = {
            t["function"]["name"]
            for t in build_tool_list(module, self._ctx())
        }
        assert names == {ToolName.CONCLUDE.value}
