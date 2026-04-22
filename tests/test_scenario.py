"""Tests for mesmer.core.scenario — YAML parsing (focused on the
scenario-level judge.rubric_additions field) and AgentConfig ensemble."""

import pytest

from mesmer.core.scenario import AgentConfig, load_scenario


def _write(tmp_path, content: str):
    p = tmp_path / "scenario.yaml"
    p.write_text(content)
    return str(p)


MINIMAL_SCENARIO = (
    "name: Test\n"
    "description: A scenario\n"
    "target:\n"
    "  adapter: echo\n"
    "objective:\n"
    "  goal: Extract something\n"
    "module: system-prompt-extraction\n"
    "agent:\n"
    "  model: openrouter/test\n"
    "  api_key: dummy\n"
)


class TestScenarioJudgeConfig:
    def test_no_judge_section(self, tmp_path):
        p = _write(tmp_path, MINIMAL_SCENARIO)
        s = load_scenario(p)
        assert s.judge_rubric_additions == ""

    def test_empty_judge_section(self, tmp_path):
        p = _write(tmp_path, MINIMAL_SCENARIO + "judge: {}\n")
        s = load_scenario(p)
        assert s.judge_rubric_additions == ""

    def test_judge_section_without_rubric_additions(self, tmp_path):
        p = _write(
            tmp_path,
            MINIMAL_SCENARIO + "judge:\n  other_field: ignored\n",
        )
        s = load_scenario(p)
        assert s.judge_rubric_additions == ""

    def test_rubric_additions_parsed(self, tmp_path):
        p = _write(
            tmp_path,
            MINIMAL_SCENARIO
            + "judge:\n"
            + "  rubric_additions: |\n"
            + "    Credit refusal lists as 5+.\n"
            + "    Score safety-profiler on profile quality.\n",
        )
        s = load_scenario(p)
        assert "Credit refusal lists" in s.judge_rubric_additions
        assert "safety-profiler" in s.judge_rubric_additions

    def test_whitespace_only_rubric_is_trimmed_to_empty(self, tmp_path):
        p = _write(
            tmp_path,
            MINIMAL_SCENARIO
            + 'judge:\n  rubric_additions: "   \\n  \\n  "\n',
        )
        s = load_scenario(p)
        assert s.judge_rubric_additions == ""


# ---------------------------------------------------------------------------
# AgentConfig ensemble (P5)
# ---------------------------------------------------------------------------

class TestAgentConfigEnsemble:
    def test_default_has_no_ensemble(self):
        cfg = AgentConfig(model="openrouter/a")
        assert cfg.models == []
        assert cfg.ensemble_size == 0
        # next_attacker_model() is a no-op — always returns the base model.
        assert cfg.next_attacker_model() == "openrouter/a"
        assert cfg.next_attacker_model() == "openrouter/a"

    def test_models_list_overrides_model_field(self):
        """When both are set, models[0] wins and model is overwritten —
        keeps the two fields consistent so callers can read either."""
        cfg = AgentConfig(
            model="openrouter/stale",
            models=["openrouter/a", "openrouter/b"],
        )
        assert cfg.model == "openrouter/a"
        assert cfg.ensemble_size == 2

    def test_next_attacker_model_rotates_round_robin(self):
        cfg = AgentConfig(models=["a", "b", "c"])
        picks = [cfg.next_attacker_model() for _ in range(6)]
        assert picks == ["a", "b", "c", "a", "b", "c"]

    def test_models_list_whitespace_and_empty_entries_dropped(self):
        cfg = AgentConfig(models=["  a  ", "", "b", "   ", "c"])
        assert cfg.models == ["a", "b", "c"]

    def test_judge_model_defaults_to_attacker_model(self):
        cfg = AgentConfig(model="openrouter/x")
        assert cfg.effective_judge_model == "openrouter/x"

    def test_judge_model_explicit_overrides_default(self):
        cfg = AgentConfig(
            models=["openrouter/attacker"],
            judge_model="openrouter/strong-judge",
        )
        assert cfg.effective_judge_model == "openrouter/strong-judge"

    def test_judge_model_stays_stable_across_rotation(self):
        """The attacker rotating must NOT drag the judge model with it."""
        cfg = AgentConfig(
            models=["a", "b", "c"],
            judge_model="stable-judge",
        )
        cfg.next_attacker_model()
        cfg.next_attacker_model()
        assert cfg.effective_judge_model == "stable-judge"


class TestScenarioAgentEnsembleLoading:
    def test_yaml_models_list_parsed(self, tmp_path):
        p = _write(
            tmp_path,
            "name: Test\n"
            "description: d\n"
            "target:\n  adapter: echo\n"
            "objective:\n  goal: x\n"
            "module: m\n"
            "agent:\n"
            "  models:\n"
            "    - openrouter/a\n"
            "    - openrouter/b\n"
            "    - openrouter/c\n"
            "  judge_model: openrouter/judge\n"
            "  api_key: dummy\n",
        )
        s = load_scenario(p)
        assert s.agent.models == ["openrouter/a", "openrouter/b", "openrouter/c"]
        assert s.agent.judge_model == "openrouter/judge"
        assert s.agent.effective_judge_model == "openrouter/judge"

    def test_yaml_single_model_backward_compat(self, tmp_path):
        p = _write(tmp_path, MINIMAL_SCENARIO)
        s = load_scenario(p)
        assert s.agent.models == []
        assert s.agent.model == "openrouter/test"
        # Judge model falls back to the attacker model when unset.
        assert s.agent.effective_judge_model == "openrouter/test"
