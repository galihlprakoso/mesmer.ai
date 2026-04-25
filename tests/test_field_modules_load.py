"""Field-technique module sanity checks.

These tests keep the six tier-0/1 modules under `modules/techniques/field/`
target-agnostic and correctly tiered. Anti-overfit guardrails live here
because the easiest way to beat a single benchmark is to quietly hardcode
its canary strings — which would tank generalisation on any other target.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

FIELD_ROOT = (
    Path(__file__).resolve().parent.parent / "modules" / "techniques" / "field"
)

EXPECTED_MODULES: dict[str, int] = {
    "direct-ask": 0,
    "instruction-recital": 0,
    "indirect-recital": 0,
    "format-shift": 0,
    "prefix-commitment": 1,
    "delimiter-injection": 1,
    "role-impersonation": 1,
}

# Tokens that would betray overfitting to the Tensor Trust extraction
# dataset. If any of these appear in a field module YAML, the module is
# cheating — bin it and rewrite.
BANNED_TOKENS = [
    "password",
    "access code",
    "tensor trust",
    "canary",
    "pre_prompt",
    "post_prompt",
]


def _load_all() -> dict[str, dict]:
    """Load every module.yaml under FIELD_ROOT and return name → parsed dict."""
    out: dict[str, dict] = {}
    for child in sorted(FIELD_ROOT.iterdir()):
        if not child.is_dir():
            continue
        yaml_path = child / "module.yaml"
        if not yaml_path.exists():
            continue
        data = yaml.safe_load(yaml_path.read_text())
        out[data["name"]] = data
    return out


class TestFieldModulesDiscovery:
    def test_all_expected_modules_exist(self):
        """Every tier-0/1 module named in the TAPER plan ships with a yaml."""
        loaded = _load_all()
        assert set(loaded.keys()) == set(EXPECTED_MODULES.keys()), (
            f"field/ directory members {sorted(loaded)} don't match expected "
            f"set {sorted(EXPECTED_MODULES)}"
        )


class TestFieldModulesTierValues:
    @pytest.mark.parametrize("module_name,expected_tier", sorted(EXPECTED_MODULES.items()))
    def test_declared_tier_matches_plan(self, module_name: str, expected_tier: int):
        """Each module.yaml declares the exact tier the TAPER plan assigns."""
        loaded = _load_all()
        assert module_name in loaded, f"{module_name} missing from field/"
        assert loaded[module_name].get("tier") == expected_tier


class TestFieldModulesAntiOverfit:
    """Regex-scan every field-module yaml for dataset-specific tokens.

    Non-negotiable: these modules must work against ANY LLM target. A
    hardcoded "password" or "canary" reference is the sign that a
    benchmark score came from cheating, not from a better attack.
    """

    def test_no_banned_tokens_in_any_field_module(self):
        offenders: dict[str, list[str]] = {}
        for child in sorted(FIELD_ROOT.iterdir()):
            yaml_path = child / "module.yaml"
            if not yaml_path.exists():
                continue
            body = yaml_path.read_text().lower()
            hits = [tok for tok in BANNED_TOKENS if re.search(rf"\b{re.escape(tok)}\b", body)]
            if hits:
                offenders[child.name] = hits
        assert not offenders, (
            "field-technique modules contain dataset-specific tokens — "
            f"overfitting risk: {offenders}"
        )

    def test_every_field_module_resets_target(self):
        """Tier-0/1 probes need a clean session — document the contract."""
        loaded = _load_all()
        for name, data in loaded.items():
            assert data.get("reset_target") is True, (
                f"{name} must declare `reset_target: true` — tier-0/1 probes "
                "must not ride on a contaminated target transcript."
            )


# ---------------------------------------------------------------------------
# target-profiler decoupling
# ---------------------------------------------------------------------------
#
# target-profiler is a generic reconnaissance module; it must not bake in
# the system-prompt-extraction leader's vocabulary or hardcode the
# specific sub-module names that leader happens to own. Those couplings
# are what produced the "direct-ask module was successful" hallucination
# in the 20260424T165043Z run (the profiler was literally instructed to
# name those strings in its exploitation hypothesis).

PROFILER_YAML = (
    Path(__file__).resolve().parent.parent
    / "modules" / "profilers" / "target-profiler" / "module.yaml"
)

# Terms that would re-couple the profiler to the extraction-specific
# leader, scenario, or dataset. The BANNED_TOKENS list above focuses on
# dataset leakage; this list focuses on "does this prompt pretend it
# knows the scenario it's running in".
PROFILER_COUPLING_TOKENS = [
    # Scenario-coupling: the profiler should describe behaviour, not
    # know what leader / objective it's serving.
    "extract the system prompt",
    "attack modules handle",
    # Module-name hardcoding: enumerating sibling modules in the prompt
    # is exactly what triggered the direct-ask hallucination.
    "instruction-recital",
    "format-shift",
    "prefix-commitment",
    "delimiter-injection",
    "role-impersonation",
    "cognitive-overload",
    "foot-in-door",
    "authority-bias",
    "narrative-transport",
    "pragmatic-reframing",
]


class TestTargetProfilerDecoupling:
    """Guardrails on the target-profiler prompt — keep it generic."""

    def test_yaml_loads(self):
        assert PROFILER_YAML.is_file(), (
            f"expected target-profiler at {PROFILER_YAML}"
        )
        data = yaml.safe_load(PROFILER_YAML.read_text())
        assert data["name"] == "target-profiler"
        assert data["tier"] == 0

    def test_no_leader_or_sibling_coupling(self):
        body = PROFILER_YAML.read_text().lower()
        hits = [
            tok for tok in PROFILER_COUPLING_TOKENS
            if tok.lower() in body
        ]
        assert not hits, (
            "target-profiler prompt contains tokens that couple it to the "
            "system-prompt-extraction leader or its sibling modules — these "
            "are the overfitting traps that produced the "
            f"'direct-ask module was successful' hallucination: {hits}"
        )

    def test_no_dataset_specific_tokens(self):
        """Same BANNED_TOKENS scan the field modules get — profiler must
        stay target-agnostic too."""
        body = PROFILER_YAML.read_text().lower()
        hits = [
            tok for tok in BANNED_TOKENS
            if re.search(rf"\b{re.escape(tok)}\b", body)
        ]
        assert not hits, (
            f"target-profiler contains dataset-specific tokens: {hits}"
        )
