# Scenarios

Attack scenario configurations in YAML format.

## Public scenarios (examples)

The `.yaml` files in this directory are example scenarios shipped with mesmer. They use dummy targets or public-facing test endpoints.

| Scenario | What it demonstrates |
|---|---|
| `extract-system-prompt.yaml` | Baseline system-prompt leakage testing against an OpenAI-compatible target. |
| `extract-dvllm-tools.yaml` | Tool/function-surface extraction against the local dvllm demo target. |
| `rag-document-injection-proof.yaml` | Source-boundary testing for RAG, ticket, document, and pasted-content workflows. |
| `full-redteam-with-execution.yaml` | Multi-stage dvllm demo: prompt recon, tool recon, indirect injection, side-effect proof. |

For a pattern library with source links and copy-paste scenario guidance,
see `docs/content/docs/recipes/research-grounded-scenarios.mdx`.

Public scenarios declare their durable artifacts explicitly. Keep
`operator_notes` in each scenario so the executive can preserve concise
human/operator discussion takeaways with `update_artifact` instead of
treating transient chat as the long-term plan.

## Private scenarios

Put your real target scenarios in `scenarios/private/`. This directory is **gitignored** — nothing inside it will ever be committed or pushed.

```bash
mkdir -p scenarios/private
cp scenarios/extract-system-prompt.yaml scenarios/private/my-target.yaml
# edit with your real target URL, API keys, etc.
mesmer run scenarios/private/my-target.yaml --verbose
```
