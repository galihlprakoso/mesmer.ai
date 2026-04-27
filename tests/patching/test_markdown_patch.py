from __future__ import annotations

import pytest

from mesmer.core.patching import MarkdownPatchError, apply_markdown_patch


def test_append_section_creates_missing_section():
    result = apply_markdown_patch(
        "## Evidence\n- first\n",
        [{"op": "append_section", "heading": "Next Step", "content": "Run proof."}],
    )

    assert "## Evidence" in result.content
    assert "## Next Step\nRun proof." in result.content
    assert result.summaries == ["created section 'Next Step'"]


def test_append_section_preserves_existing_content():
    result = apply_markdown_patch(
        "## Evidence\n- first\n",
        [{"op": "append_section", "heading": "Evidence", "content": "- second"}],
    )

    assert "## Evidence\n- first\n\n- second\n" == result.content


def test_replace_and_delete_section():
    replaced = apply_markdown_patch(
        "## A\nold\n\n## B\nkeep\n",
        [{"op": "replace_section", "heading": "A", "content": "new"}],
    )
    deleted = apply_markdown_patch(
        replaced.content,
        [{"op": "delete_section", "heading": "B"}],
    )

    assert "## A\nnew" in replaced.content
    assert "## B" not in deleted.content


def test_line_operations():
    result = apply_markdown_patch(
        "- stale\n- keep\n",
        [
            {"op": "replace_matching_line", "match": "stale", "replacement": "- fresh"},
            {"op": "insert_after", "match": "fresh", "content": "- inserted"},
            {"op": "delete_matching_line", "match": "keep"},
        ],
    )

    assert result.content == "- fresh\n- inserted\n"


def test_rejects_missing_section():
    with pytest.raises(MarkdownPatchError, match="section not found"):
        apply_markdown_patch("", [{"op": "replace_section", "heading": "Missing", "content": "x"}])


def test_rejects_unknown_operation():
    with pytest.raises(MarkdownPatchError, match="unsupported operation"):
        apply_markdown_patch("", [{"op": "rewrite_universe"}])
