from __future__ import annotations

import pytest

from mesmer.core.patching import MarkdownPatchError, MarkdownPatchOperation, apply_markdown_patch


def test_append_section_creates_missing_section():
    result = apply_markdown_patch(
        "## Evidence\n- first\n",
        [
            {
                "op": MarkdownPatchOperation.APPEND_SECTION.value,
                "heading": "Next Step",
                "content": "Run proof.",
            }
        ],
    )

    assert "## Evidence" in result.content
    assert "## Next Step\nRun proof." in result.content
    assert result.summaries == ["created section 'Next Step'"]


def test_append_section_preserves_existing_content():
    result = apply_markdown_patch(
        "## Evidence\n- first\n",
        [
            {
                "op": MarkdownPatchOperation.APPEND_SECTION.value,
                "heading": "Evidence",
                "content": "- second",
            }
        ],
    )

    assert "## Evidence\n- first\n\n- second\n" == result.content


def test_replace_and_delete_section():
    replaced = apply_markdown_patch(
        "## A\nold\n\n## B\nkeep\n",
        [{"op": MarkdownPatchOperation.REPLACE_SECTION.value, "heading": "A", "content": "new"}],
    )
    deleted = apply_markdown_patch(
        replaced.content,
        [{"op": MarkdownPatchOperation.DELETE_SECTION.value, "heading": "B"}],
    )

    assert "## A\nnew" in replaced.content
    assert "## B" not in deleted.content


def test_line_operations():
    result = apply_markdown_patch(
        "- stale\n- keep\n",
        [
            {
                "op": MarkdownPatchOperation.REPLACE_MATCHING_LINE.value,
                "match": "stale",
                "replacement": "- fresh",
            },
            {
                "op": MarkdownPatchOperation.INSERT_AFTER.value,
                "match": "fresh",
                "content": "- inserted",
            },
            {"op": MarkdownPatchOperation.DELETE_MATCHING_LINE.value, "match": "keep"},
        ],
    )

    assert result.content == "- fresh\n- inserted\n"


def test_rejects_missing_section():
    with pytest.raises(MarkdownPatchError, match="section not found"):
        apply_markdown_patch(
            "",
            [
                {
                    "op": MarkdownPatchOperation.REPLACE_SECTION.value,
                    "heading": "Missing",
                    "content": "x",
                }
            ],
        )


def test_rejects_unknown_operation():
    with pytest.raises(MarkdownPatchError, match="unsupported operation"):
        apply_markdown_patch("", [{"op": "rewrite_universe"}])
