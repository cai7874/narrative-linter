# Portfolio preparation notes

Prepared locally on 2026-10-08 from coursework main snapshot `4917c6b4bf88c66c252cdc049a19311203a1a7dd`.

The original coursework snapshot remains available in Git history. These fixes were prepared separately from the original checkout and are now included as a follow-up repository update. No live model requests were performed during preparation.

Changes made with Codex assistance:

- Removed false inventory requirements for bare attacks on another character.
- Unified supported action aliases so dead-character checks cover movement verbs.
- Prevented events with errors or warnings from advancing replay state.
- Required co-presence for physical transfers and distinguished use from consumption.
- Reported unknown actions and missing event fields instead of silently passing them.
- Validated source line/span traceability; exposed extracted events in the dashboard.
- Added coverage-aware verdicts, 14 regression tests, and 17 synthetic acceptance cases.
- Replaced author-specific startup paths with repository-relative instructions.

Validation: 28 unit tests passed; 17/17 synthetic acceptance cases matched; Gradio dashboard constructed successfully without starting a server. These are software checks, not measured LLM accuracy. Dependency installation from a fresh environment and live model inference were not tested.
