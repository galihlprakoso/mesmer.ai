"""Tests for mesmer.core.scenario — YAML parsing (focused on the
scenario-level judge.rubric_additions field)."""

import pytest

from mesmer.core.scenario import load_scenario


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
