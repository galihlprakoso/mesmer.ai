## Run objective

{objective}

## Known target traits

{traits_block}

## Currently active hypotheses (do NOT duplicate)

{active_block}

## Recent attempt history (last few attempts, oldest → newest)

{history_block}

{library_block}

## Your task

Propose up to 4 NEW falsifiable hypotheses that the planner should test next.
Each should claim something the existing active list does not already cover.

Return:

```
{{
  "hypotheses": [
    {{
      "claim": "<single-sentence falsifiable claim>",
      "description": "<2-3 sentences: what would support / refute it>",
      "family": "<from family vocabulary above>",
      "confidence": <0.0..1.0 prior estimate>
    }}
  ]
}}
```

Emit an empty `hypotheses` list if no genuinely new hypothesis is warranted —
a noisy proposal is worse than silence.
