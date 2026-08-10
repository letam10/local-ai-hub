# Milestone 8C — Creative Recipe Intelligence Core

Milestone 8C is a static planning layer for image and video creation.  It
helps a future UI, API or Node Studio adapter compose a safe prompt recipe and
understand compatibility before any model, job, media tool or process is
authorized.  This milestone never launches a workload and every public
projection states `execution: not_run`.

## Contracts

`src/shared/schemas/creative_recipes.py` exports closed Draft 2020-12 schemas
and runtime validators for:

- `creative-recipe-catalog.v1`: a bounded catalog of recipes, style packs and
  static target cards;
- `creative-recipe.v1`: typed prompt template, slots, variants, style
  references, media kind, compatibility declaration and static constraints;
- `creative-style-pack.v1`: safe positive/negative prompt additions and
  supported media kinds;
- `creative-prompt-slot.v1` and `creative-prompt-variant.v1`: typed values,
  defaults, enum/bounds and deterministic variant overrides;
- `creative-target-card.v1`: a fixed capability/model-family compatibility
  card, not a runtime health claim;
- `creative-generation-intent.v1`: a detached `dry_run: true` prompt plan;
- `creative-recipe-lint-finding.v1`: fixed code, severity, status, reason and
  action for each static issue;
- `creative-compatibility-report.v1`: static target checks with missing
  capabilities and truthful status.

The Draft schemas enforce the closed field/type/bound surface.  Cross-field
invariants that JSON Schema cannot express portably (for example a default
belonging to a caller-declared enum, slot-reference resolution, duplicate
entity IDs and catalog references) are rechecked by the fail-closed Python
validator before any public DTO is returned.

Unknown fields, duplicate IDs, duplicate catalog identities, duplicate JSON
keys, non-finite numbers, invalid UTF-8, oversized descriptors, unsafe text,
paths, URLs, secrets, commands, payload markers, weights and arbitrary runtime
configuration fail closed.  IDs and SemVer values are opaque and bounded.

## Discovery and planning

Only `creative_recipes/catalog/*.creative-recipe-catalog.json` under the fixed
repository-managed root is discovered.  The bounded reader opens once and
requests at most `MAX_DESCRIPTOR_BYTES + 1`; it refuses symlinks, containment
escapes, replacement and mutation.  A duplicate catalog, recipe, style-pack
or target identity is unavailable/ambiguous; unversioned references are also
refused when multiple managed versions share one opaque ID.  Discovery never
chooses by file name order.

`lint_recipe` performs deterministic substitution, required-slot/type/enum
checks, style-pack and target references, prompt scrub/length checks and
constraint checks.  `build_compatibility_report` compares only declared target
capabilities, model families and media kind.  `build_generation_intent` and
`plan_recipe_variants` return detached dry-run DTOs; they do not import a
module, resolve a model, run a graph, call a process, read media or start a
job.  An unavailable video target remains unavailable even when its prompt is
otherwise valid.

## Samples and CLI

The tracked starter catalog contains product image, cinematic portrait, image
edit/upscale and short-video concept recipes, plus clean, portrait and
storyboard style packs.  The video target is intentionally unavailable until a
separate authorized runtime smoke exists.

```text
python scripts/validate_creative_recipes.py --format json
python scripts/validate_creative_recipes.py --format markdown
python scripts/validate_creative_recipes.py --catalog-id catalog_creative_starter --version 1.0.0
```

The CLI has no arbitrary input/output path and emits only scrubbed JSON or
Markdown.  Import/export helpers are detached and canonical; they do not write
the managed tree.

`src/services/creative_recipe_intelligence/reports.py` offers the same
revalidation-first projection for compatibility, lint, intent and discovery
Markdown without accepting a client-built report dictionary.

## Future integration DTO

Future UI/API/Node Studio code should consume the returned `recipe`, `findings`,
`report` and `intent` objects as read-only DTOs.  It should display opaque IDs,
fixed statuses, reason/action text and safe prompt text, then pass a selected
intent to a separately authorized owner.  It must not add execution fields,
paths, commands, model payloads or client-built findings to these contracts.
This milestone intentionally makes no changes to those shared integration
lanes.
