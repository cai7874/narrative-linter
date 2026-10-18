# World State Extraction Validation

Implementation follows `WORLD_STATE_EXTRACTION_PLAN.md` for a single shared story
world. The core entry points are `extract_world_facts`, `prepare_import`, and
`confirm_import`. Confirmation uses a server-side preview, `BEGIN IMMEDIATE`, a
saved-world snapshot check, and one transaction for facts and provenance.
The snapshot check is deliberately conservative: any world-record edit requires
a fresh preview, including edits to records outside the selected proposals.

## Deterministic checks

- `python -m unittest discover -s eval -v`: 83 tests pass.
- `python evaluate.py`: 17/17 existing synthetic acceptance cases pass.
- `python demo_world_import.py`: disposable offline import and audit example passes.
- `git diff --check`: passes.

Tests cover migrations from the legacy and intermediate schemas, unknown versus
empty inventory, partial possessions, selected-only persistence, name
confirmation, conflicting candidates, ordered transfers, per-stage provenance,
transaction rollback, duplicate saves, stale previews, descriptive rules,
relationships, source validation, provider failures, and direct Gradio callbacks.

The new page was checked in Safari for layout, tab navigation, editable table
controls, collapsed advanced options, the shared inspector, and initial disabled
confirmation. Extraction and persistence tests ran directly in Python, without
using the webpage to run model requests or save test facts.

## Live extraction, separate from verifier correctness

The configured LM Studio model was `qwen/qwen3.5-9b` at the local compatible
endpoint. The final six-case evaluation passed 4/6 manually annotated cases:

| Case | Result |
| --- | --- |
| Explicit settings | Passed |
| Transfer | Failed: extra recipient ownership addition after the correct transfer |
| Omitted facts | Passed |
| Negation | Failed: negation marked correctly, but the item value was omitted |
| Ambiguous reference | Passed |
| Contradiction | Passed |

`reports/world_live_evaluation.json` contains final raw proposals, named semantic
checks, extraction errors, and suggested human corrections. The first evaluation
(2/6) is preserved in `reports/world_live_evaluation_first_pass.json`; the initial
exploratory combined passage is in `reports/world_live_initial.json`. Iteration
used this small development set, so the final result is not a held-out benchmark.

The final explicit-settings model response was also imported into a disposable
SQLite database after explicit candidate selection and name confirmation. The
following consistency check used the saved entity successfully, and replay did
not change it. Evidence: `reports/world_live_import_integration.json`.

No live evaluation modified the user's saved world. Format/source validation
establishes traceability, not semantic entailment. Human review remains required.
The optional live CLI exits nonzero when any annotated case fails, so its quality
failures are not confused with passing deterministic regression tests.

## Readable preview update

The review keeps explicit selection, preview, and confirmation. The primary
preview now shows before/after values, source excerpts, blocked facts, unchanged
facts, and a count of pending changes. JSON is available in a collapsed technical
details section. Editing any review input invalidates the frozen preview and
disables confirmation.

Validation: 83 deterministic tests pass with `python -m unittest discover -s eval
-q`. Added coverage checks readable values and evidence, transfer participants,
unchanged and blocked facts, stale review handling, and escaped user content.
Safari layout review used a temporary-database fixture with prepared candidates;
no model extraction or real-world confirmation was performed through the browser.
