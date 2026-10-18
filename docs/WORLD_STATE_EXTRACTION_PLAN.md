# World State Extraction — Development Plan

Status: Implemented. Deterministic regression coverage and a separate live-model evaluation are included. Live extraction still requires human review; see reports/world_live_evaluation.json.

## Objective

Add a World State Extraction mode to Narrative Linter. Users can enter background notes or existing narrative, review structured facts extracted by an LLM, edit the proposed changes, and explicitly confirm them before they are saved.

The application serves one story project. Both modes share the existing SQLite database; project selection and multiple-project storage are out of scope.

| Mode | Input | Purpose | Database behavior |
|---|---|---|---|
| World State Extraction | Background settings and existing narrative | Extract characters, locations, inventory, relationships, attributes, and world rules; preview and confirm changes | Insert or update confirmed facts |
| Story Consistency Check | A new narrative passage | Check extracted events against established facts and replay valid state changes temporarily | Read established facts; do not persist replay results |

## Current Behavior

- `app.py` provides a Gradio dashboard for manually editing entities and checking narrative.
- `core/extractor.py` extracts action claims through an OpenAI-compatible model endpoint.
- `core/database.py` stores entities and keyword-based world rules in SQLite.
- `core/verifier.py` replays supported events in memory. Replay never writes narrative outcomes back to the database.
- There is no natural-language world import, fact review screen, or confirmed import transaction.

The new feature complements the existing event extractor. State extraction and event extraction need separate schemas and prompts.

## User Workflow

1. Open the World State Extraction tab.
2. Paste background settings or an existing story passage.
3. Click Extract Proposed Facts. The application reads the current world and requests structured extraction from the configured model.
4. Inspect proposed entities, locations, possessions, attributes, relationships, and rules alongside their source excerpts.
5. Resolve ambiguous names, edit extracted values, and select which changes to accept.
6. Review a final diff showing additions, updates, conflicts, and skipped facts.
7. Click Confirm and Save. The application validates and saves the selected changes in one transaction.
8. Refresh the shared world inspector. Subsequent story checks use the saved state.

Extraction, editing, cancellation, and failed requests must not change established world facts. Manual entity editing remains available.

## Extraction Contract

Introduce a dedicated structured response for candidate world facts. Each candidate includes:

- A candidate identifier and fact type.
- A subject reference and, where needed, an object entity or location reference.
- A field and structured value, such as status, location, possession, or relationship type.
- An exact source excerpt and its 1-based source line number.
- Whether the statement is an explicit fact, a transition, an uncertain statement, or an unresolved reference.

The model proposes facts; application code resolves identifiers, computes diffs, validates data, and writes SQL. Model output must never contain executable SQL.

Extraction rules:

- Do not invent unstated facts. Missing fields stay unknown or require user input.
- Separate explicit negation from positive facts. Do not convert “does not own a sword” into sword ownership.
- Mark uncertain, hypothetical, quoted, or conflicting statements for review rather than treating them as established facts.
- Require user resolution for ambiguous aliases and pronouns. Offer existing entities as possible matches without silently merging them.
- Preserve narrative order for explicit transitions. For example, “A gave the key to B” proposes a transfer, not simultaneous ownership by both characters.
- If flashbacks, alternative timelines, or conflicting statements prevent a clear final state, require a user decision.
- Validate that each source excerpt occurs on its stated input line. This establishes traceability, not extraction correctness.

Use the existing model configuration and OpenAI-compatible client settings initially. Keep this feature's prompt and response models separate from `Claim` and `ExtractionResult`.

## Shared Database Design

Add versioned, non-destructive schema migrations before introducing new tables or changing field constraints. Preserve existing entity identifiers, records, and manual editing behavior.

| Data | Proposed storage | Initial checking support |
|---|---|---|
| Characters | Existing `entities` table | Existing supported entity checks |
| Status, location, inventory | Existing entity fields, with explicit unknown-value handling | Check only known facts; report insufficient information where relevant |
| Attributes | Existing entity attributes JSON | Stored and displayed; no automatic attribute reasoning |
| Locations | New `locations` table with stable IDs and names | Preserve existing name-based location checks initially |
| Relationships | New `relationships` table referencing entity IDs | Stored and displayed; new relationship checks are out of scope |
| World rules | Existing `world_rules` plus descriptive-rule support | Keyword checks only for explicitly configured compatible rules |
| Import source and provenance | New import and accepted-change records | Show the source of accepted facts and previous values |

Implementation constraints:

- Current entity records require a status and location. Update storage, forms, and verification to support unknown values consistently; never default an unstated status to alive or invent a location.
- Preserve the distinction between unknown inventory and a known empty inventory. An omitted inventory must not erase an existing inventory or imply that no items are owned.
- Define whether each proposal adds, removes, or replaces inventory entries. Show the exact effect before confirmation.
- New entities require a user-confirmed name; stable database IDs are generated by application code.
- Store descriptive rules even when the current verifier cannot enforce them. Mark them visibly as not automatically checked. Do not claim that arbitrary prose rules become enforceable merely because they were imported.
- Preserve the current seeded world for compatibility. Do not reinsert deleted demo entities or rules on every startup; make demo initialization a one-time or explicit action.
- Persist accepted changes and their provenance in the same transaction. Include an import ID, submitted source text, timestamp, source span, target record, and before/after values.

Detailed migration names and SQL should be finalized during implementation after reviewing compatibility with existing tests and database files.

## Merge and Confirmation Policy

Compute proposals against the saved database rather than allowing the model to overwrite records directly.

- New fact: propose an insertion.
- Identical fact: show as unchanged and avoid duplicate records.
- Missing input field: leave the saved value untouched.
- Different existing value: show the previous and proposed values and require explicit selection.
- Ambiguous entity match or incompatible candidate facts: block the affected change until resolved.
- Unsupported or unresolved fact: retain it in the review results with an explanation; do not silently discard it or save it as certain.
- Unselected change: leave the database untouched.

Immediately before saving, check whether affected records changed since the preview was generated. If they did, refresh the affected diffs and require renewed confirmation. A stale preview must not overwrite a newer manual edit.

Save the selected, validated changes atomically. Any validation or database error rolls back the entire confirmation transaction. Prevent duplicate submission of an already committed import.

## Interface

Organize the Gradio application into two tabs sharing the same world inspector:

1. World State Extraction: source input, extraction status, editable candidate tables, source evidence, conflict resolution, final diff, and Confirm and Save.
2. Story Consistency Check: existing narrative input, diagnostics, verdict, latency, and extracted action claims.

Show a clear distinction between extracted proposals and saved world facts. Disable confirmation while extraction is running or selected proposals contain unresolved validation errors. Allow cancellation and retry without losing established data.

## Implementation Steps

1. **Database foundations:** add migrations, unknown-value semantics, locations, relationships, descriptive rules, provenance, and transactional import persistence. Adapt current verification and forms to unknown data.
2. **State extraction:** add strict response models, a dedicated prompt, source validation, and readable provider/schema errors, reusing existing client configuration.
3. **Proposal and merge logic:** implement entity resolution, deterministic diffs, conflict detection, inventory operations, stale-preview detection, and duplicate-save protection.
4. **Review interface:** add the extraction tab, editable proposals, source evidence, selective acceptance, final confirmation, and inspector refresh.
5. **Integration and documentation:** preserve the story-checking workflow, document supported extraction and checking behavior, and add an offline example using fixed model responses.

Suggested new modules are `core/world_extractor.py` for extraction schemas and provider interaction, and `core/world_import.py` for proposal preparation and confirmed persistence. Keep `core/extractor.py` focused on narrative action claims. Final module boundaries should follow the implementation's actual complexity.

## Validation and Acceptance Criteria

Use deterministic fixtures and a fake model client for automated tests. Cover behavior at the extraction, merge, database, and application boundaries.

- Explicit facts produce editable proposals with valid source evidence.
- Malformed output, invalid source spans, or provider failures leave world facts unchanged.
- Unknown fields remain unknown and do not erase established values.
- Existing entities can be updated without duplicate characters; ambiguous matches require resolution.
- Conflicts display old and proposed values and remain unsaved until explicitly accepted.
- An explicit transfer produces the correct proposed ownership changes.
- Cancellation and rejected changes do not modify established facts.
- A confirmed selection persists only the selected changes and their provenance atomically.
- A failed save rolls back; repeated confirmation does not create duplicate imports.
- A stale preview cannot overwrite a newer database edit.
- Relationships and descriptive rules are visibly distinguished from automatically enforced checks.
- Saved world changes are used by the next consistency audit, while audit replay remains non-persistent.
- Existing regression tests and synthetic acceptance cases continue to pass, with intentional unknown-state behavior changes documented.

Perform a separate live-model evaluation on a small, manually annotated set covering explicit settings, transitions, omissions, negation, ambiguous names, and contradictions. Report extraction errors and review corrections separately from deterministic verifier results. Passing mocked tests does not demonstrate live extraction quality.

## Out of Scope for This Release

- Multiple story projects or project switching.
- Automatic saving immediately after model extraction.
- Persisting new chapter outcomes from Story Consistency Check.
- Automatic resolution of flashbacks, unreliable narrators, or alternative timelines.
- General-purpose enforcement of arbitrary natural-language rules.
- Comprehensive quantity, travel-time, relationship, or resource-consumption reasoning.

## Completion Definition

A user can paste story background, inspect and correct evidence-backed world proposals, confirm selected changes into the existing shared database, and check a new passage against those saved facts. No world changes occur without explicit confirmation, and the interface accurately states which saved facts the verifier can enforce.
