# Validation

Run all commands below from the repository root after completing the
[environment setup](../README.md#run).

## Offline checks

```bash
python -m unittest discover -s eval -v
python evaluate.py
python demo.py
python demo_world_import.py
```

The current automated suite contains 85 tests. The synthetic acceptance evaluator
matches 17/17 cases and writes `reports/acceptance.json`. Both demos use temporary
databases and require no model server.

Coverage includes source validation, state replay, migrations, unknown and partial
inventories, selective import, source provenance, rollback, duplicate confirmation,
stale previews, and interface callbacks. These checks validate application logic;
they do not measure real model accuracy or establish browser usability.

## Live model checks

Start an OpenAI-compatible model server with JSON Schema support. See the
[application README](../README.md#run) for model and endpoint configuration.

To evaluate real event extraction on the synthetic acceptance cases:

```bash
python evaluate.py --live --output reports/live_evaluation_new.json
```

To evaluate world-state extraction on six annotated development cases:

```bash
python -m eval.live_world_extraction --run --output reports/world_live_evaluation_new.json
```

Choose a new output filename for each live run to preserve recorded evidence.
World-state evaluation exits nonzero if any annotated case fails. Neither
evaluator imports facts into the user's saved database.

## Retained evaluation evidence

| File | Scope |
| --- | --- |
| [`acceptance.json`](../reports/acceptance.json) | 17 synthetic cases using gold event records; evaluates verifier and replay behavior |
| [`world_live_evaluation.json`](../reports/world_live_evaluation.json) | Recorded `qwen/qwen3.5-9b` extraction responses and semantic checks; 4/6 cases passed |
| [`world_live_import_integration.json`](../reports/world_live_import_integration.json) | A real explicit-settings response imported into a temporary database, followed by an audit with supplied claims |

The recorded world-state run passed explicit settings, omitted facts, ambiguous
references, and contradictions. Transfer failed because of duplicate recipient
ownership; negation failed because the item value was omitted. Prompt iteration
used these same six cases, so the result is not a held-out benchmark or an
estimate of general accuracy. Source-span validation proves traceability, not
semantic correctness. Human review remains necessary.

## Manual demonstration

Before presenting, verify the complete browser workflow with the intended model:
extract background facts, review and edit proposals, preview and save selected
facts, then check a new narrative against the saved world. Also try malformed or
unsupported input and inspect how errors and coverage limits are communicated.

Previous interface checks used prepared proposals. The retained integration
evidence does not establish a complete browser-and-model end-to-end run.
Story audits replay events in memory and must leave saved facts unchanged.
