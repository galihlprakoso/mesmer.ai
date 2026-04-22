# Scenarios

Attack scenario configurations in YAML format.

## Public scenarios (examples)

The `.yaml` files in this directory are example scenarios shipped with mesmer. They use dummy targets or public-facing test endpoints.

## Private scenarios

Put your real target scenarios in `scenarios/private/`. This directory is **gitignored** — nothing inside it will ever be committed or pushed.

```bash
mkdir -p scenarios/private
cp scenarios/extract-system-prompt.yaml scenarios/private/my-target.yaml
# edit with your real target URL, API keys, etc.
mesmer run scenarios/private/my-target.yaml --verbose
```
