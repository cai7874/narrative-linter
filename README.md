# Narrative Linter

The application uses an OpenAI-compatible endpoint. By default it connects to
LM Studio at `http://127.0.0.1:1234/v1` and requests the
`qwen/qwen3.5-9b` model.

## LM Studio setup

1. Load `qwen/qwen3.5-9b` in LM Studio.
2. Start the local server on port `1234`.
3. Start the application:

```bash
cd /Users/cai/Desktop/checker
.venv/bin/python app.py
```

The default API key is the placeholder `lm-studio`, so no environment
variables are required for the default LM Studio setup.

## Custom provider or model

Override any default with environment variables:

```bash
export NARRATIVE_LINTER_API_KEY="provider-api-key"
export NARRATIVE_LINTER_BASE_URL="http://127.0.0.1:1234/v1"
export NARRATIVE_LINTER_MODEL="provider-model-id"
.venv/bin/python app.py
```

`OPENAI_API_KEY` and `OPENAI_BASE_URL` are also accepted as compatibility
aliases. `NARRATIVE_LINTER_*` variables take precedence.

## Audit behavior

The verifier replays extracted claims in input order against an in-memory copy
of the SQLite world state. It validates claimed locations, updates movement
destinations, and tracks item acquisition, transfer, dropping, and consumption.
Unknown subject and interaction-target entities are reported as
`ENTITY_NOT_FOUND`. The SQLite seed remains unchanged; replay is scoped to the
current audit.
