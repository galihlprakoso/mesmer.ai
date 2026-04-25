"""Tests for mesmer.core.scenario — YAML parsing (focused on the
scenario-level judge.rubric_additions field) and AgentConfig ensemble."""


from mesmer.core.constants import ScenarioMode
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
            + "    Score target-profiler on profile quality.\n",
        )
        s = load_scenario(p)
        assert "Credit refusal lists" in s.judge_rubric_additions
        assert "target-profiler" in s.judge_rubric_additions

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


# ---------------------------------------------------------------------------
# ScenarioMode (C0, C6)
# ---------------------------------------------------------------------------

class TestScenarioModeParsing:
    def test_default_mode_is_trials(self, tmp_path):
        """Omitting ``mode:`` yields TRIALS — backward-compatible with
        every scenario file written before continuous mode existed."""
        p = _write(tmp_path, MINIMAL_SCENARIO)
        s = load_scenario(p)
        assert s.mode == ScenarioMode.TRIALS

    def test_explicit_trials_parses(self, tmp_path):
        p = _write(tmp_path, MINIMAL_SCENARIO + "mode: trials\n")
        s = load_scenario(p)
        assert s.mode == ScenarioMode.TRIALS

    def test_explicit_continuous_parses(self, tmp_path):
        p = _write(tmp_path, MINIMAL_SCENARIO + "mode: continuous\n")
        s = load_scenario(p)
        assert s.mode == ScenarioMode.CONTINUOUS

    def test_mode_is_case_insensitive(self, tmp_path):
        """YAML is sometimes authored sloppily — don't punish Continuous
        vs continuous. Silent uppercase handling keeps users on the rails."""
        p = _write(tmp_path, MINIMAL_SCENARIO + "mode: CONTINUOUS\n")
        s = load_scenario(p)
        assert s.mode == ScenarioMode.CONTINUOUS

    def test_unknown_mode_degrades_to_trials(self, tmp_path):
        """A typo should not silently flip a scenario into continuous mode
        — the safer legacy behaviour wins."""
        p = _write(tmp_path, MINIMAL_SCENARIO + "mode: persistent\n")
        s = load_scenario(p)
        assert s.mode == ScenarioMode.TRIALS

    def test_empty_mode_string_degrades_to_trials(self, tmp_path):
        p = _write(tmp_path, MINIMAL_SCENARIO + 'mode: ""\n')
        s = load_scenario(p)
        assert s.mode == ScenarioMode.TRIALS


# ---------------------------------------------------------------------------
# AgentConfig context budget + compression (C7)
# ---------------------------------------------------------------------------

class TestAgentConfigContextBudget:
    def test_default_values(self):
        """All compression fields default to no-op values so existing
        scenarios keep their exact behaviour."""
        cfg = AgentConfig(model="x/y", api_key="k")
        assert cfg.max_context_tokens == 0
        assert cfg.compression_keep_recent == 10
        assert cfg.compression_target_ratio == 0.6
        assert cfg.compression_model == ""

    def test_valid_fields_preserved(self):
        cfg = AgentConfig(
            model="x/y", api_key="k",
            max_context_tokens=50_000,
            compression_keep_recent=6,
            compression_target_ratio=0.4,
            compression_model="x/cheap",
        )
        assert cfg.max_context_tokens == 50_000
        assert cfg.compression_keep_recent == 6
        assert cfg.compression_target_ratio == 0.4
        assert cfg.compression_model == "x/cheap"

    def test_negative_max_context_tokens_clamped_to_zero(self):
        """Negative cap makes no sense — degrades to auto-resolve."""
        cfg = AgentConfig(model="x/y", api_key="k", max_context_tokens=-5)
        assert cfg.max_context_tokens == 0

    def test_keep_recent_less_than_one_clamped(self):
        """Must keep at least one recent turn — otherwise compression just
        eats the entire transcript each firing."""
        cfg = AgentConfig(model="x/y", api_key="k", compression_keep_recent=0)
        assert cfg.compression_keep_recent == 1

    def test_invalid_keep_recent_falls_back_to_default(self):
        cfg = AgentConfig(model="x/y", api_key="k", compression_keep_recent="not-a-number")
        assert cfg.compression_keep_recent == 10

    def test_target_ratio_out_of_range_falls_back(self):
        """Ratios outside (0.0, 1.0] degrade to the default."""
        c1 = AgentConfig(model="x/y", api_key="k", compression_target_ratio=0.0)
        c2 = AgentConfig(model="x/y", api_key="k", compression_target_ratio=1.5)
        c3 = AgentConfig(model="x/y", api_key="k", compression_target_ratio=-0.3)
        assert c1.compression_target_ratio == 0.6
        assert c2.compression_target_ratio == 0.6
        assert c3.compression_target_ratio == 0.6

    def test_invalid_target_ratio_string_falls_back(self):
        cfg = AgentConfig(model="x/y", api_key="k", compression_target_ratio="half")
        assert cfg.compression_target_ratio == 0.6

    def test_non_string_compression_model_coerced_to_empty(self):
        cfg = AgentConfig(model="x/y", api_key="k", compression_model=None)
        assert cfg.compression_model == ""

    def test_effective_max_context_tokens_uses_explicit_cap(self):
        cfg = AgentConfig(model="x/y", api_key="k", max_context_tokens=12345)
        assert cfg.effective_max_context_tokens("any/model") == 12345

    def test_effective_max_context_tokens_uses_litellm_lookup(self):
        """Zero explicit cap → ask litellm.get_max_tokens and subtract 10%."""
        from unittest.mock import patch as _patch
        cfg = AgentConfig(model="x/y", api_key="k", max_context_tokens=0)
        with _patch("litellm.get_max_tokens", return_value=200_000):
            assert cfg.effective_max_context_tokens("anthropic/big") == 180_000

    def test_effective_max_context_tokens_returns_zero_on_lookup_failure(self):
        """If litellm can't resolve the model, return 0 so compression is
        disabled rather than firing on a bogus tiny cap."""
        from unittest.mock import patch as _patch
        cfg = AgentConfig(model="x/y", api_key="k", max_context_tokens=0)
        with _patch("litellm.get_max_tokens", side_effect=Exception("boom")):
            assert cfg.effective_max_context_tokens("unknown/model") == 0
        with _patch("litellm.get_max_tokens", return_value=None):
            assert cfg.effective_max_context_tokens("unknown/model") == 0
        with _patch("litellm.get_max_tokens", return_value=-1):
            assert cfg.effective_max_context_tokens("unknown/model") == 0

    def test_effective_compression_model_cascade(self):
        """compression_model wins; then judge_model; then attacker model."""
        c_explicit = AgentConfig(
            model="att/x", judge_model="judge/y",
            compression_model="comp/z", api_key="k",
        )
        assert c_explicit.effective_compression_model() == "comp/z"

        c_judge = AgentConfig(model="att/x", judge_model="judge/y", api_key="k")
        assert c_judge.effective_compression_model() == "judge/y"

        c_attacker = AgentConfig(model="att/x", api_key="k")
        assert c_attacker.effective_compression_model() == "att/x"


class TestAgentConfigYamlParsing:
    def test_yaml_parses_context_budget_fields(self, tmp_path):
        p = _write(
            tmp_path,
            "name: t\ndescription: d\n"
            "target:\n  adapter: echo\n"
            "objective:\n  goal: g\n"
            "module: m\n"
            "agent:\n"
            "  model: openrouter/x\n"
            "  api_key: dummy\n"
            "  max_context_tokens: 80000\n"
            "  compression_keep_recent: 8\n"
            "  compression_target_ratio: 0.5\n"
            "  compression_model: openrouter/summariser\n",
        )
        s = load_scenario(p)
        assert s.agent.max_context_tokens == 80000
        assert s.agent.compression_keep_recent == 8
        assert s.agent.compression_target_ratio == 0.5
        assert s.agent.compression_model == "openrouter/summariser"

    def test_yaml_invalid_values_fall_back(self, tmp_path):
        """Bogus YAML values use defaults — don't crash the run."""
        p = _write(
            tmp_path,
            "name: t\ndescription: d\n"
            "target:\n  adapter: echo\n"
            "objective:\n  goal: g\n"
            "module: m\n"
            "agent:\n"
            "  model: openrouter/x\n"
            "  api_key: dummy\n"
            "  max_context_tokens: -50\n"
            "  compression_keep_recent: 0\n"
            "  compression_target_ratio: 3.0\n",
        )
        s = load_scenario(p)
        assert s.agent.max_context_tokens == 0
        assert s.agent.compression_keep_recent == 1
        assert s.agent.compression_target_ratio == 0.6


class TestTargetThrottleYamlParsing:
    """Scenario YAMLs can declare a per-target throttle block for provider-side
    rate-limit gating. Parsed onto :class:`TargetConfig.throttle` so
    ``create_target`` can build the backing :class:`KeyPool`."""

    def test_target_throttle_block_parses(self, tmp_path):
        p = _write(
            tmp_path,
            "name: t\ndescription: d\n"
            "target:\n"
            "  adapter: openai\n"
            "  base_url: http://127.0.0.1\n"
            "  model: m\n"
            "  api_key: k\n"
            "  throttle:\n"
            "    max_rpm: 12\n"
            "    max_concurrent: 3\n"
            "    max_wait_seconds: 120\n"
            "objective:\n  goal: g\n"
            "module: m\n"
            "agent:\n  model: a/b\n  api_key: dummy\n",
        )
        s = load_scenario(p)
        assert s.target.throttle is not None
        assert s.target.throttle.max_rpm == 12
        assert s.target.throttle.max_concurrent == 3
        assert s.target.throttle.max_wait_seconds == 120.0

    def test_target_without_throttle_leaves_field_none(self, tmp_path):
        """Legacy scenarios without a ``throttle:`` block keep the field
        ``None`` — ``OpenAITarget`` then takes the unthrottled path."""
        p = _write(tmp_path, MINIMAL_SCENARIO)
        s = load_scenario(p)
        assert s.target.throttle is None
