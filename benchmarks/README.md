# mesmer benchmarks

One canonical spec. One target. One attacker. Every published result
ships with a fixed Methodology / Contamination Posture / Limitations
template so no number goes out without its context.

## Directory layout

```
benchmarks/
  README.md           ← this file — reproducibility contract
  specs/
    tensor-trust-extraction.yaml   ← the canonical spec
  datasets/
    README.md         ← dataset provenance, licenses, schemas
    cache/            ← local JSONL cache (fetched on demand)
  results/            ← timestamped outputs
    <iso>-<spec-version>-<target>__mesmer.jsonl
    <iso>-<spec-version>-<target>__baseline.jsonl
    <iso>-<spec-version>-summary.json
    <iso>-<spec-version>-README.md   ← headline table + Methodology
                                     +  Contamination + Reproducibility
                                     +  Limitations
```

## Reproducibility contract

Every claim traces back to a committed artifact that anyone can re-run:

1. **Datasets are pinned by SHA256.** `expected_sha256` in the spec is
   verified on every run; mismatches abort with a clear error. See
   [datasets/README.md](datasets/README.md) for provenance.
2. **Model versions are pinned.** Target and attacker reference models
   by snapshot / tag. Ollama manifest digests can be recorded in the
   summary JSON by the operator.
3. **The judge is code.** `mesmer/bench/canary.py` is a deterministic
   substring matcher with unit tests. No LLM, no randomness, same
   inputs always score the same way. The in-loop LLM judge guides
   mesmer's next move but does NOT decide benchmark success.
4. **Seeds are committed.** Every per-trial JSONL row carries the seed
   applied to Python's `random`. Provider-side LLM sampling is not
   made deterministic — that variance is averaged over
   `trials_per_row` and reported via stderr.
5. **Mesmer version is stamped.** Spec version + mesmer package version
   + dataset SHA256 are written into the summary JSON.
6. **Contamination posture is mandatory.** Every spec must declare
   `dataset_release_date`, `upstream_license`, `target_model_cutoff`,
   `attacker_model_cutoff`, and a free-text `risk_assessment`. Specs
   without this block fail to load. The posture is rendered verbatim
   into every published README.
7. **Limitations ship with every result.** The markdown renderer
   emits a canonical Limitations section so no reader has to guess
   what the numbers do and don't claim.
8. **Sample IDs are recorded.** The summary JSON lists every
   `sample_id` tested, so downstream tools can reconstruct which
   defenses were covered.
9. **One command to reproduce.** `mesmer bench <spec.yaml>` is the
   full command. The spec is the full input.

## Running the benchmark

```bash
# Fast smoke (3 rows × 1 trial):
uv run mesmer bench benchmarks/specs/tensor-trust-extraction.yaml \
  --sample 3 --trials 1

# Iteration run (50 rows × 3 trials, ~current spec default):
uv run mesmer bench benchmarks/specs/tensor-trust-extraction.yaml

# Publication run — full 569 rows × 3 trials:
uv run mesmer bench benchmarks/specs/tensor-trust-extraction.yaml \
  --sample 0

# Force a fresh dataset fetch (bypasses the local cache):
uv run mesmer bench benchmarks/specs/tensor-trust-extraction.yaml --download

# Skip baseline arm when you only want mesmer numbers:
uv run mesmer bench benchmarks/specs/tensor-trust-extraction.yaml --no-baseline
```

## Prerequisites

- **Ollama**: `ollama pull llama3.1:8b-instruct-q4_K_M`, then `ollama serve`.
- **Attacker**: `export GEMINI_API_KEY=...`. Swap the attacker `model:`
  in the spec to any LiteLLM-reachable model if you want a different
  brain — but bump the `spec.version` so old artifacts stay traceable.

## Reading the output

- `<iso>-<spec-version>-summary.json` — aggregate stats, contamination
  posture, sample IDs tested, dataset SHA256.
- `<iso>-<spec-version>-README.md` — headline table plus Methodology,
  Contamination, Reproducibility, Limitations. Drop-in for publication.
- `<iso>-<spec-version>-<target>__<arm>.jsonl` — per-trial detail
  (sample_id, turns-to-success, tokens, seed, error).

## Authoring new specs

Clone the existing YAML and modify. Required top-level blocks:

- `module:` — which mesmer module is under test.
- `dataset:` — upstream URL + SHA256 + `row_schema` mapping onto
  mesmer's canonical `{pre_prompt, post_prompt, canary,
  baseline_attack, sample_id}`. See
  [datasets/README.md](datasets/README.md) for provenance.
- `targets:` — any OpenAI-compatible endpoint (or `openai_compat`
  + Ollama).
- `attacker:` — the red-team brain (LiteLLM-reachable).
- `contamination_posture:` — **required**. Must declare all of:
  - `dataset_release_date` — when the upstream data was first public.
  - `upstream_license` — confirm against upstream LICENSE on fetch.
  - `target_model_cutoff` — provider-stated training cutoff.
  - `attacker_model_cutoff` — same, for the attacker.
  - `risk_assessment` — free-text paragraph explaining how
    contamination could skew the numbers and how to read them.

Specs without a complete `contamination_posture` block raise a clear
`ValueError` at load time.

The only judge currently supported is `canary-substring` (literal
substring match on the access code, case-insensitive). Other
deterministic judges (regex, JSON-field, tool-call detection) are
follow-up work.
