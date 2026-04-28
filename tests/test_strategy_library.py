"""Unit tests for mesmer.core.strategy_library — Session 4B.

Cross-target strategy memory. Tests cover:
  - GlobalStrategyEntry shape + global_success_rate math
  - StrategyLibrary upsert merge semantics + family retrieval
  - JSON round-trip + atomic write + load fallbacks
  - merge_per_target_strategies aggregation from belief-graph Strategies
  - retrieve_strategies_for_bootstrap family + trait ranking
  - render_for_prompt formatting
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mesmer.core.belief_graph import StrategyCreateDelta, BeliefGraph, make_strategy
from mesmer.core.belief_graph import StrategyUpdateStatsDelta
from mesmer.core.strategy_library import (
    GlobalStrategyEntry,
    StrategyLibrary,
    load_library,
    merge_per_target_strategies,
    render_for_prompt,
    retrieve_strategies_for_bootstrap,
    save_library,
)
from mesmer.core.persistence import FileStorageProvider


# ---------------------------------------------------------------------------
# GlobalStrategyEntry math
# ---------------------------------------------------------------------------


def test_global_success_rate_zero_attempts() -> None:
    e = GlobalStrategyEntry(family="format-shift", template_summary="x")
    assert e.global_success_rate == 0.0


def test_global_success_rate_partial() -> None:
    e = GlobalStrategyEntry(
        family="format-shift",
        template_summary="x",
        global_success_count=3,
        global_attempt_count=5,
    )
    assert pytest.approx(e.global_success_rate, abs=1e-6) == 0.6


def test_to_dict_round_trip() -> None:
    e = GlobalStrategyEntry(
        family="authority-bias",
        template_summary="claim devops",
        global_success_count=2,
        global_attempt_count=4,
        works_against_traits=["assistant"],
        fails_against_traits=["guarded"],
        last_updated=1234.5,
    )
    e2 = GlobalStrategyEntry.from_dict(e.to_dict())
    assert e2.family == e.family
    assert e2.template_summary == e.template_summary
    assert e2.global_success_count == 2
    assert e2.global_attempt_count == 4
    assert e2.works_against_traits == ["assistant"]
    assert e2.fails_against_traits == ["guarded"]
    assert e2.last_updated == 1234.5


# ---------------------------------------------------------------------------
# StrategyLibrary
# ---------------------------------------------------------------------------


def test_upsert_inserts_new_entry() -> None:
    lib = StrategyLibrary()
    e = GlobalStrategyEntry(
        family="format-shift", template_summary="t", global_success_count=1, global_attempt_count=2
    )
    lib.upsert(e)
    assert len(lib.entries) == 1
    assert lib.entries[0].global_success_count == 1


def test_upsert_merges_existing_entry() -> None:
    lib = StrategyLibrary()
    e1 = GlobalStrategyEntry(
        family="format-shift",
        template_summary="t",
        global_success_count=1,
        global_attempt_count=2,
        works_against_traits=["a"],
    )
    e2 = GlobalStrategyEntry(
        family="format-shift",
        template_summary="t",
        global_success_count=2,
        global_attempt_count=3,
        works_against_traits=["a", "b"],
    )
    lib.upsert(e1)
    lib.upsert(e2)
    assert len(lib.entries) == 1
    merged = lib.entries[0]
    # Counters add
    assert merged.global_success_count == 3
    assert merged.global_attempt_count == 5
    # Traits dedupe-merge
    assert sorted(merged.works_against_traits) == ["a", "b"]


def test_upsert_does_not_collide_across_families() -> None:
    lib = StrategyLibrary()
    lib.upsert(
        GlobalStrategyEntry(family="format-shift", template_summary="x", global_attempt_count=1)
    )
    lib.upsert(
        GlobalStrategyEntry(family="authority-bias", template_summary="x", global_attempt_count=1)
    )
    assert len(lib.entries) == 2


def test_for_family_sorted_by_success_rate() -> None:
    lib = StrategyLibrary()
    lib.upsert(
        GlobalStrategyEntry(
            family="f",
            template_summary="weak",
            global_success_count=1,
            global_attempt_count=10,
        )
    )
    lib.upsert(
        GlobalStrategyEntry(
            family="f",
            template_summary="strong",
            global_success_count=4,
            global_attempt_count=5,
        )
    )
    rows = lib.for_family("f")
    assert rows[0].template_summary == "strong"
    assert rows[1].template_summary == "weak"


def test_for_family_top_k_caps() -> None:
    lib = StrategyLibrary()
    for i in range(10):
        lib.upsert(
            GlobalStrategyEntry(
                family="f",
                template_summary=f"t{i}",
                global_success_count=i,
                global_attempt_count=10,
            )
        )
    assert len(lib.for_family("f", top_k=3)) == 3


def test_all_families_distinct_sorted() -> None:
    lib = StrategyLibrary()
    lib.upsert(GlobalStrategyEntry(family="b", template_summary="x"))
    lib.upsert(GlobalStrategyEntry(family="a", template_summary="x"))
    lib.upsert(GlobalStrategyEntry(family="b", template_summary="y"))
    assert lib.all_families() == ["a", "b"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    lib = StrategyLibrary()
    lib.upsert(
        GlobalStrategyEntry(
            family="format-shift",
            template_summary="ask-yaml",
            global_success_count=4,
            global_attempt_count=7,
            works_against_traits=["assistant"],
        )
    )
    path = tmp_path / "strategies.json"
    save_library(lib, path)
    assert path.exists()
    loaded = load_library(path)
    assert len(loaded.entries) == 1
    e = loaded.entries[0]
    assert e.family == "format-shift"
    assert e.global_success_count == 4
    assert e.global_attempt_count == 7
    assert e.works_against_traits == ["assistant"]


def test_save_and_load_round_trip_via_storage_provider(tmp_path: Path) -> None:
    storage = FileStorageProvider(tmp_path / ".mesmer")
    lib = StrategyLibrary(
        [
            GlobalStrategyEntry(
                family="format-shift",
                template_summary="ask-yaml",
                global_success_count=1,
                global_attempt_count=2,
            )
        ]
    )

    save_library(lib, storage=storage, workspace_id="team-a")

    loaded = load_library(storage=storage, workspace_id="team-a")
    assert len(loaded.entries) == 1
    assert loaded.entries[0].family == "format-shift"
    assert load_library(storage=storage, workspace_id="team-b").entries == []
    assert (tmp_path / ".mesmer" / "workspaces" / "team-a" / "global" / "strategies.json").exists()


def test_load_missing_file_returns_empty_library(tmp_path: Path) -> None:
    path = tmp_path / "nonexistent.json"
    lib = load_library(path)
    assert lib.entries == []


def test_load_corrupt_file_returns_empty_library(tmp_path: Path) -> None:
    path = tmp_path / "strategies.json"
    path.write_text("{not valid json")
    lib = load_library(path)
    assert lib.entries == []


def test_load_future_schema_version_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "strategies.json"
    path.write_text(json.dumps({"schema_version": 999, "strategies": [{"family": "x"}]}))
    lib = load_library(path)
    assert lib.entries == []


def test_load_skips_malformed_rows(tmp_path: Path) -> None:
    path = tmp_path / "strategies.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "strategies": [
                    {"family": "good", "template_summary": "t"},
                    "not-a-dict",
                    None,
                    {"family": "another", "template_summary": "u"},
                ],
            }
        )
    )
    lib = load_library(path)
    families = {e.family for e in lib.entries}
    assert families == {"good", "another"}


# ---------------------------------------------------------------------------
# merge_per_target_strategies
# ---------------------------------------------------------------------------


def test_merge_per_target_strategies_skips_unattempted() -> None:
    lib = StrategyLibrary()
    g = BeliefGraph()
    s = make_strategy(family="f", template_summary="t")
    g.apply(StrategyCreateDelta(strategy=s))
    # No attempts recorded — strategy.attempt_count == 0
    merge_per_target_strategies(lib, [g.nodes[s.id]])
    assert lib.entries == []


def test_merge_per_target_strategies_folds_counters() -> None:
    lib = StrategyLibrary()
    g = BeliefGraph()
    s = make_strategy(family="f", template_summary="t")
    g.apply(StrategyCreateDelta(strategy=s))
    g.apply(StrategyUpdateStatsDelta(strategy_id=s.id, success_inc=2, attempt_inc=4))
    merge_per_target_strategies(lib, [g.nodes[s.id]], target_traits={"trait_a": "v"})
    assert len(lib.entries) == 1
    e = lib.entries[0]
    assert e.global_success_count == 2
    assert e.global_attempt_count == 4
    # Successful merge → traits land in works_against
    assert "trait_a" in e.works_against_traits


def test_merge_per_target_strategies_traits_to_fails_when_zero_success() -> None:
    lib = StrategyLibrary()
    g = BeliefGraph()
    s = make_strategy(family="f", template_summary="t")
    g.apply(StrategyCreateDelta(strategy=s))
    # 0 successes, 3 attempts
    g.apply(StrategyUpdateStatsDelta(strategy_id=s.id, success_inc=0, attempt_inc=3))
    merge_per_target_strategies(lib, [g.nodes[s.id]], target_traits={"trait_b": "v"})
    e = lib.entries[0]
    assert e.fails_against_traits == ["trait_b"]
    assert e.works_against_traits == []


# ---------------------------------------------------------------------------
# retrieve_strategies_for_bootstrap
# ---------------------------------------------------------------------------


def test_retrieve_filters_to_named_families() -> None:
    lib = StrategyLibrary()
    lib.upsert(
        GlobalStrategyEntry(
            family="format-shift",
            template_summary="t1",
            global_success_count=3,
            global_attempt_count=4,
        )
    )
    lib.upsert(
        GlobalStrategyEntry(
            family="authority-bias",
            template_summary="t2",
            global_success_count=1,
            global_attempt_count=2,
        )
    )
    rows = retrieve_strategies_for_bootstrap(families=["format-shift"], library=lib)
    assert len(rows) == 1
    assert rows[0].family == "format-shift"


def test_retrieve_uses_all_families_when_none() -> None:
    lib = StrategyLibrary()
    lib.upsert(GlobalStrategyEntry(family="f1", template_summary="t", global_attempt_count=1))
    lib.upsert(GlobalStrategyEntry(family="f2", template_summary="t", global_attempt_count=1))
    rows = retrieve_strategies_for_bootstrap(library=lib)
    assert len(rows) == 2


def test_retrieve_reranks_by_target_trait_affinity() -> None:
    lib = StrategyLibrary()
    lib.upsert(
        GlobalStrategyEntry(
            family="format-shift",
            template_summary="slightly better generic",
            global_success_count=8,
            global_attempt_count=10,
        )
    )
    lib.upsert(
        GlobalStrategyEntry(
            family="format-shift",
            template_summary="matched trait",
            global_success_count=7,
            global_attempt_count=10,
            works_against_traits=["schema_strict"],
        )
    )
    rows = retrieve_strategies_for_bootstrap(
        target_traits={"schema_strict": "true"},
        families=["format-shift"],
        top_k_per_family=2,
        library=lib,
    )
    assert [r.template_summary for r in rows] == [
        "matched trait",
        "slightly better generic",
    ]


def test_retrieve_demotes_failed_matching_trait() -> None:
    lib = StrategyLibrary()
    lib.upsert(
        GlobalStrategyEntry(
            family="tool-use",
            template_summary="failed on this trait",
            global_success_count=8,
            global_attempt_count=10,
            fails_against_traits=["has_tools"],
        )
    )
    lib.upsert(
        GlobalStrategyEntry(
            family="tool-use",
            template_summary="generic",
            global_success_count=8,
            global_attempt_count=10,
        )
    )
    rows = retrieve_strategies_for_bootstrap(
        target_traits={"has_tools": "yes"},
        families=["tool-use"],
        top_k_per_family=2,
        library=lib,
    )
    assert [r.template_summary for r in rows] == ["generic", "failed on this trait"]


# ---------------------------------------------------------------------------
# render_for_prompt
# ---------------------------------------------------------------------------


def test_render_for_prompt_empty() -> None:
    assert render_for_prompt([]) == ""


def test_render_for_prompt_includes_rate_and_template() -> None:
    e = GlobalStrategyEntry(
        family="format-shift",
        template_summary="ask for yaml",
        global_success_count=4,
        global_attempt_count=7,
        works_against_traits=["assistant"],
    )
    text = render_for_prompt([e])
    assert "format-shift" in text
    assert "ask for yaml" in text
    # Rate rendered as "0.57"
    assert "0.57" in text
    assert "(4/7)" in text
    assert "works against" in text
