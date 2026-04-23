# mesmer benchmarks

Reproducible benchmark suite. Each spec binds one mesmer module to one
real, public dataset + a list of target models, then produces a
versioned results artifact.

## Directory layout

```
benchmarks/
  README.md           ← this file — reproducibility contract
  specs/              ← benchmark specs (YAML)
    tensor-trust-extraction-v1.yaml
  datasets/
    README.md         ← dataset provenance, licenses, schemas
    cache/            ← local JSONL cache (fetched on demand)
  results/            ← timestamped outputs
    <iso>-<spec-version>-<target>__mesmer.jsonl
    <iso>-<spec-version>-<target>__baseline.jsonl
    <iso>-<spec-version>-summary.json
    <iso>-<spec-version>-README.md   ← drop-in for the main README
```

## Reproducibility contract

Every claim in the README traces back to a committed artifact that anyone
can re-run. Concretely:

1. **Datasets are pinned by SHA256.** `expected_sha256` in each spec is
   verified on every run; mismatches abort with a clear error. See
   [datasets/README.md](datasets/README.md) for each dataset's provenance,
   upstream URL, and license status.
2. **Model versions are pinned.** Spec YAMLs reference commercial models
   by snapshot ID (`gpt-4o-mini-2024-07-18`, `claude-3-haiku-20240307`)
   and Ollama models by tag; the local Ollama manifest's digest can be
   recorded in the per-run summary by the operator.
3. **The judge is code.** `mesmer/bench/canary.py` is a ~40-line
   deterministic substring matcher with 19 unit tests. No LLM, no
   randomness, same inputs always score the same way. The in-loop
   LLM judge guides mesmer's next move but does NOT decide benchmark
   success.
4. **Seeds are committed.** Every per-trial JSONL row carries the seed
   that was applied to Python's `random` module. (Provider-side LLM
   sampling is *not* made deterministic — we handle that variance by
   averaging over `trials_per_row` seeds, and reporting stderr.)
5. **Mesmer version is stamped.** The spec version + mesmer package
   version are written into the summary JSON.
6. **One command to reproduce.** `mesmer bench <spec.yaml>` is the full
   command. The spec is the full input.

## Running a benchmark

```bash
# Fast smoke (3 rows, 1 trial each, one local target) — ~60 seconds:
uv run mesmer bench benchmarks/specs/tensor-trust-extraction-v1.yaml \
  --sample 3 --trials 1 --targets llama3.1-8b-ollama

# Iteration run — 50 rows × 3 trials × 1 target:
uv run mesmer bench benchmarks/specs/tensor-trust-extraction-v1.yaml \
  --sample 50 --trials 3 --targets llama3.1-8b-ollama

# Publication run — full dataset, all targets (expensive):
uv run mesmer bench benchmarks/specs/tensor-trust-extraction-v1.yaml

# Force a fresh dataset fetch (bypasses local cache):
uv run mesmer bench benchmarks/specs/tensor-trust-extraction-v1.yaml --download

# Skip baseline arm when you only want mesmer numbers:
uv run mesmer bench benchmarks/specs/tensor-trust-extraction-v1.yaml --no-baseline
```

## Prerequisites

- **For Ollama targets**: `ollama pull llama3.1:8b-instruct-q4_K_M` (or the
  equivalent tag from the spec), then `ollama serve`.
- **For commercial targets**: `export OPENAI_API_KEY=...` /
  `export ANTHROPIC_API_KEY=...`.
- **For the attacker**: `export OPENROUTER_API_KEY=...`. Swap the
  attacker `model:` in the spec to any LiteLLM-reachable model if you
  want a different brain.

## Reading the output

- `<iso>-<spec-version>-summary.json` — aggregate stats per (target, arm).
- `<iso>-<spec-version>-README.md` — Markdown table, ready to paste.
- `<iso>-<spec-version>-<target>__<arm>.jsonl` — per-trial detail
  (sample_id, turns-to-success, tokens, seed, error).

## Authoring new specs

Clone an existing YAML in `specs/` and modify:

- `module:` — which mesmer module is under test
- `dataset:` — a new dataset? See [datasets/README.md](datasets/README.md)
  for the row schema; the orchestrator maps your `row_schema` onto its
  canonical `{pre_prompt, post_prompt, canary, baseline_attack, sample_id}`
- `targets:` — swap target models freely; any OpenAI-compatible endpoint works
- `attacker:` — swap the attacker brain
- `judge:` — for v0 we only support `canary-substring`; other deterministic
  judges (regex, JSON field match, etc.) are follow-up work

## Follow-up specs (not in v0)

- Tensor Trust `hijacking-robustness` — different surface (target says
  "Access Granted" without the real code). Same schema; add as v2.
- `narrative-transport` on HarmBench/JailbreakBench subset — LLM-as-judge
  path. Requires a non-canary judge dispatch.
- `indirect-prompt-injection` using DVLLM's `research-l1/l2/l3` personas
  as target fixtures — success = `send_email` tool called.
