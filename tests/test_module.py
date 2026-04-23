"""Tests for mesmer.core.module — YAML loader + ModuleConfig defaults."""

from mesmer.core.module import ModuleConfig, load_module_config


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
