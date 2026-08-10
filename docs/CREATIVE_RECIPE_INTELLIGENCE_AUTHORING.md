# Creative Recipe Intelligence Authoring

Recipes are bounded prompt metadata, not executable workflows.  Author only
the fields in the published `*.v1` contracts and keep all descriptors in the
repository-managed `creative_recipes/catalog` root.

## Recipe and slots

Each recipe declares `media_kind`, a prompt template, a negative prompt,
typed `slots`, optional `variants`, style-pack IDs, target/capability
compatibility and fixed parameter constraints.  Slot IDs are opaque and typed:
`text`, `enum`, `number`, `integer`, `boolean`, `style`, `aspect_ratio`,
`duration` or `seed`.  Required slots must have a safe value before a
generation intent can be planned.  Numeric slots use finite bounded values;
enum values and defaults must match the declared type.

Use `{{slot_id}}` placeholders only for declared slots.  A variant may override
the prompt, negative prompt, slot defaults and style IDs but cannot add fields
or arbitrary settings.  Keep prompts concise and descriptive; do not include
URLs, paths, credentials, command syntax, base64/media payloads, checkpoint or
weight references, host names or private data.

## Style packs and targets

Style packs contain only bounded positive/negative prompt text, tags, preview
metadata, license and supported media kinds.  Target cards contain fixed
capability/model-family allowlists and a truthful static status.  `partial`
means the declaration is usable for planning but not runtime verified;
`unavailable` means a future execution owner must provide separately authorized
evidence.  Never mark a card operational merely because a model name is known.

## Lint, compatibility and intent

Use the Python validator and Draft 2020-12 schema together.  Then call
`lint_recipe` with slot values and optional target/parameters.  It reports fixed
codes for missing slots, type/enum errors, unresolved placeholders, unsafe or
oversized text, style/media conflicts, target/capability/model mismatches and
constraint conflicts.  Findings carry only opaque subject IDs, counts and
fixed reason/action text.

`build_compatibility_report` is a static comparison of declarations.  It does
not inspect a GPU, model registry, process, filesystem or network.  A valid
composition can produce a `planned` dry-run intent only when the static target
is not unavailable.  `build_generation_intent` always emits `dry_run: true` and
`execution: not_run`; it is never a command or job payload.

## Catalog maintenance and safe exchange

Use `safe_import_recipe_catalog` for bytes/UTF-8 JSON and
`export_recipe_catalog` for deterministic canonical bytes.  Duplicate JSON
keys, NaN/Infinity, BOM, invalid UTF-8 and over-limit input fail closed without
echoing parser exceptions or unsafe values.  Managed discovery refuses duplicate
catalog/entity identity rather than selecting by filename.  Import/export and
linting are detached operations and must not write a user's workflow tree or
download/install a model or dependency.

The CLI reads only the fixed repository root and prints scrubbed JSON/Markdown.
Future UI/API/Node Studio adapters should pass these DTOs through unchanged and
keep any runtime-specific request in their own separately authorized contract.

For in-process projections, `reports.py` provides detached Markdown helpers
for discovery, lint findings, compatibility reports and generation intents.
They revalidate the DTO, render only opaque IDs/statuses/codes/counts, and
always state `execution: not_run`.
