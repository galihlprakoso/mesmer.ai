"""Tests for mesmer.core.module — YAML loader + ModuleConfig defaults."""

import pytest

from mesmer.core.constants import ToolName
from mesmer.core.errors import InvalidModuleConfig
from mesmer.core.module import DEFAULT_TIER, ModuleConfig, load_module_config


class TestJudgeRubricField:
    def test_default_is_empty(self):
        m = ModuleConfig(name="x")
        assert m.judge_rubric == ""

    def test_yaml_module_parses_judge_rubric(self, tmp_path):
        module_dir = tmp_path / "probe"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: probe\n"
            "description: test\n"
            "system_prompt: do stuff\n"
            "judge_rubric: |\n"
            "  Score on profile quality, not extraction.\n"
            "  1-2 = no profile, 7-8 = comprehensive map.\n"
        )
        cfg = load_module_config(module_dir)
        assert cfg is not None
        assert cfg.name == "probe"
        assert "profile quality" in cfg.judge_rubric
        assert "comprehensive map" in cfg.judge_rubric

    def test_yaml_module_without_judge_rubric_defaults_to_empty(self, tmp_path):
        module_dir = tmp_path / "nope"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: nope\n"
            "description: test\n"
            "system_prompt: do stuff\n"
        )
        cfg = load_module_config(module_dir)
        assert cfg is not None
        assert cfg.judge_rubric == ""


class TestResetTargetField:
    def test_default_is_false(self):
        m = ModuleConfig(name="x")
        assert m.reset_target is False

    def test_yaml_module_parses_reset_target_true(self, tmp_path):
        module_dir = tmp_path / "fresh"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: fresh\n"
            "description: test\n"
            "system_prompt: do stuff\n"
            "reset_target: true\n"
        )
        cfg = load_module_config(module_dir)
        assert cfg is not None
        assert cfg.reset_target is True

    def test_yaml_module_without_reset_target_defaults_false(self, tmp_path):
        module_dir = tmp_path / "stateful"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: stateful\n"
            "description: test\n"
            "system_prompt: do stuff\n"
        )
        cfg = load_module_config(module_dir)
        assert cfg is not None
        assert cfg.reset_target is False

    def test_missing_yaml_returns_none(self, tmp_path):
        module_dir = tmp_path / "empty"
        module_dir.mkdir()
        assert load_module_config(module_dir) is None


class TestToolPolicyField:
    def test_yaml_module_parses_tool_policy(self, tmp_path):
        module_dir = tmp_path / "planner"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: planner\n"
            "description: test\n"
            "tool_policy:\n"
            "  dispatch_submodules: false\n"
            "  builtin:\n"
            "    - conclude\n"
        )
        cfg = load_module_config(module_dir)
        assert cfg is not None
        actor = cfg.as_actor()
        assert actor.tool_policy is not None
        assert actor.tool_policy.dispatch_submodules is False
        assert actor.tool_policy.builtin == [ToolName.CONCLUDE.value]

    def test_yaml_module_without_policy_uses_standard_module_policy(self, tmp_path):
        module_dir = tmp_path / "default"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: default\n"
            "description: test\n"
        )
        cfg = load_module_config(module_dir)
        assert cfg is not None
        actor = cfg.as_actor()
        assert actor.tool_policy is not None
        assert actor.tool_policy.dispatch_submodules is False
        assert actor.tool_policy.builtin == [
            ToolName.SEND_MESSAGE.value,
            ToolName.LIST_ARTIFACTS.value,
            ToolName.READ_ARTIFACT.value,
            ToolName.SEARCH_ARTIFACTS.value,
            ToolName.UPDATE_ARTIFACT.value,
            ToolName.CONCLUDE.value,
        ]


class TestTierField:
    """`tier` describes module complexity for BeliefGraph planning.

    Modules without the field default to 2 (cognitive). Field-technique
    modules can declare tier 0 or 1 so planners can prefer low-friction
    attempts before multi-turn cognitive attacks.
    """

    def test_default_is_2_on_dataclass(self):
        """Dataclass default keeps every cognitive module at tier 2."""
        m = ModuleConfig(name="x")
        assert m.tier == DEFAULT_TIER == 2

    def test_yaml_without_tier_defaults_to_2(self, tmp_path):
        """Existing module.yaml files need zero edits."""
        module_dir = tmp_path / "legacy"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: legacy\n"
            "description: test\n"
            "system_prompt: do stuff\n"
        )
        cfg = load_module_config(module_dir)
        assert cfg is not None
        assert cfg.tier == 2

    def test_yaml_parses_tier_values(self, tmp_path):
        """All four legal tiers (0..3) round-trip through the loader."""
        for tier in (0, 1, 2, 3):
            module_dir = tmp_path / f"t{tier}"
            module_dir.mkdir()
            (module_dir / "module.yaml").write_text(
                f"name: t{tier}\n"
                f"description: test\n"
                f"tier: {tier}\n"
            )
            cfg = load_module_config(module_dir)
            assert cfg is not None
            assert cfg.tier == tier

    def test_out_of_range_tier_raises(self, tmp_path):
        """Tier outside 0..3 fails loud — a typo shouldn't silently collapse to 2."""
        module_dir = tmp_path / "bogus"
        module_dir.mkdir()
        (module_dir / "module.yaml").write_text(
            "name: bogus\n"
            "description: test\n"
            "tier: 9\n"
        )
        with pytest.raises(InvalidModuleConfig) as exc:
            load_module_config(module_dir)
        assert exc.value.module_name == "bogus"
        assert exc.value.field == "tier"
        assert exc.value.value == 9
