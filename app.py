"""Gradio review workspace for world facts and narrative consistency checks."""

from __future__ import annotations

import json
import sqlite3
import time

import gradio as gr

from core.database import (
    DEFAULT_DATABASE_PATH,
    initialize_database,
    list_accepted_changes,
    list_entities,
    list_locations,
    list_imports,
    list_relationships,
    list_rules,
    upsert_entity,
)
from core.extractor import ExtractionConfigurationError, ExtractionResponseError, extract_claims
from core.audit import audit_claims
from core.world_extractor import WorldExtractionResult, extract_world_facts
from core.world_import import confirm_import, prepare_import
from core.world_preview import EMPTY_PREVIEW, render_world_preview


WORKSPACE_CSS = """
.gradio-container { max-width: 1440px !important; background: #f7f9fc; color: #233249; }
#workspace-title h1 { font-family: Georgia, serif; font-size: 2.5rem; font-weight: 500; }
#workspace-title p { max-width: 72ch; color: #52647b; }
#saved-world { margin-top: 24px; }
#saved-world h2 { border-left: 4px solid #237363; padding-left: 12px; }
#review-preview { border-left: 3px solid #2864b4; padding: 8px 18px; }
.preview-empty, .preview-note { color: #52647b; }
.preview-change { padding: 18px 0; border-top: 1px solid #d8e0eb; }
.preview-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.preview-heading h4 { margin: 0; font-size: 1.1rem; }
.preview-state { color: #237363; font-size: .85rem; white-space: nowrap; }
.state-blocked, .preview-error { color: #a32936; }
.state-unchanged { color: #52647b; }
.preview-field { margin: 12px 0; }
.preview-values { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 6px; }
.preview-values > div { padding: 10px 12px; background: #edf2f8; overflow-wrap: anywhere; white-space: pre-wrap; }
.preview-values > div:last-child { background: #eaf5f1; }
.preview-values span, .preview-change blockquote span { display: block; font-size: .8rem; color: #52647b; margin-bottom: 4px; }
.preview-owner { color: #233249; font-weight: 600; }
.preview-change blockquote { margin: 14px 0 0; padding: 8px 12px; border-left: 2px solid #d8e0eb; white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 640px) { .preview-values { grid-template-columns: 1fr; gap: 6px; } .preview-heading { flex-wrap: wrap; } }
.gradio-container button:focus-visible, .gradio-container textarea:focus-visible {
    outline: 3px solid #2864b4; outline-offset: 3px;
}
@media (max-width: 640px) { #workspace-title h1 { font-size: 1.9rem; } }
"""


def _entity_rows() -> list[list]:
    return [
        [entity["id"], entity["name"], entity["status"] or "Unknown",
         entity["location"] or "Unknown", json.dumps(entity["inventory"], ensure_ascii=False),
         json.dumps(entity["attributes"], ensure_ascii=False)]
        for entity in list_entities(DEFAULT_DATABASE_PATH)
    ]


def refresh_entities() -> list[list]:
    return _entity_rows()


def refresh_world():
    rules = [dict(rule, checking=("Keyword check" if rule.get("forbidden_action") else
                                "Descriptive only; not automatically checked"))
             for rule in list_rules(DEFAULT_DATABASE_PATH)]
    return (_entity_rows(), list_locations(DEFAULT_DATABASE_PATH),
            list_relationships(DEFAULT_DATABASE_PATH), rules,
            {"imports": list_imports(DEFAULT_DATABASE_PATH),
             "accepted_changes": list_accepted_changes(DEFAULT_DATABASE_PATH)})


def save_entity(entity_id: str, name: str, status: str, location: str, inventory: str, attributes: str):
    try:
        parsed_inventory = json.loads(inventory or "null")
        parsed_attributes = json.loads(attributes or "{}")
        if (parsed_inventory is not None and not isinstance(parsed_inventory, list)) or not isinstance(parsed_attributes, dict):
            raise ValueError("Inventory must be a JSON array or null; attributes must be a JSON object")
        upsert_entity(entity_id, name, None if status == "Unknown" else status,
                      location.strip() or None, parsed_inventory, parsed_attributes, DEFAULT_DATABASE_PATH)
        return _entity_rows(), "Entity saved."
    except (ValueError, sqlite3.Error) as exc:
        return _entity_rows(), f"Save failed: {exc}"


def audit_narrative(text: str):
    started = time.perf_counter()
    try:
        extraction = extract_claims(text)
        audit = audit_claims(extraction.claims)
        return audit["report"], audit["verdict"], _latency(started), extraction.model_dump_json(indent=2)
    except (ExtractionConfigurationError, ExtractionResponseError, ValueError) as exc:
        return f"[ERROR] {exc}", "AUDIT FAILED", _latency(started), ""


def _latency(started: float) -> str:
    return f"{(time.perf_counter() - started) * 1000:.2f} ms"


def _tokens(value: str) -> list[str]:
    return [part.strip() for line in value.splitlines() for part in line.split(",") if part.strip()]



def candidate_table_rows(candidates_json: str, selected: str = "") -> list[list]:
    """Render canonical candidates and explicit acceptance as editable table rows."""
    if not candidates_json:
        return []
    extraction = WorldExtractionResult.model_validate_json(candidates_json)
    accepted = set(_tokens(selected))
    return [[c.id in accepted, c.id, c.fact_type, c.subject, c.object_ref or "",
             c.field or "", json.dumps(c.value, ensure_ascii=False), c.operation,
             c.certainty, c.text_span, c.line_number] for c in extraction.candidates]


def sync_candidate_table(candidates_json: str, selected: str):
    """Apply advanced edits to the primary table and invalidate its old preview."""
    try:
        return candidate_table_rows(candidates_json, selected), *invalidate_preview()
    except ValueError as exc:
        return [], None, None, gr.update(interactive=False), f"Proposal JSON is invalid: {exc}"


def edit_candidate_table(rows: list[list]):
    """Validate table edits before exposing them to preview or confirmation."""
    try:
        candidates, accepted = [], []
        for index, row in enumerate(rows or [], start=1):
            if len(row) != 11:
                raise ValueError(f"Row {index} must have eleven columns")
            accept, candidate_id, fact_type, subject, object_ref, field, value, operation, certainty, span, line = row
            try:
                parsed_value = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Row {index}: Value must be valid JSON (quote text values)") from exc
            if isinstance(line, bool) or not isinstance(line, (int, float)) or int(line) != line:
                raise ValueError(f"Row {index}: Line must be an integer")
            candidate = dict(id=candidate_id, fact_type=fact_type, subject=subject,
                             object_ref=object_ref or None, field=field or None,
                             value=parsed_value, operation=operation, certainty=certainty,
                             text_span=span, line_number=int(line))
            candidates.append(candidate)
            if accept is True:
                accepted.append(candidate_id)
        extraction = WorldExtractionResult.model_validate({"candidates": candidates})
        return extraction.model_dump_json(indent=2), ", ".join(accepted), *invalidate_preview()
    except (ValueError, TypeError) as exc:
        # Clear canonical input so an invalid table can never preview old valid data.
        return "", "", None, None, gr.update(interactive=False), f"Table needs correction: {exc}"


def extract_proposals(text: str):
    """Extract proposals without accepting or persisting any candidate."""
    try:
        world = {"entities": list_entities(DEFAULT_DATABASE_PATH),
                 "locations": list_locations(DEFAULT_DATABASE_PATH),
                 "rules": list_rules(DEFAULT_DATABASE_PATH)}
        result = extract_world_facts(text, existing_world=world)
        return (result.model_dump_json(indent=2),
                f"Extracted {len(result.candidates)} proposals. Edit the table and check Accept for the facts to save.",
                None, None, gr.update(interactive=False), "", "")
    except (ExtractionConfigurationError, ExtractionResponseError, ValueError, RuntimeError) as exc:
        return "", f"Extraction failed: {exc}", None, None, gr.update(interactive=False), "", ""


def preview_proposals(text: str, candidates_json: str, selected: str, names: str):
    """Freeze the reviewed selection and show every resulting database effect."""
    try:
        selected_ids = _tokens(selected)
        if not selected_ids:
            raise ValueError("Check Accept for at least one proposal")
        extraction = WorldExtractionResult.model_validate_json(candidates_json)
        preview = prepare_import(text, extraction, selected_ids=selected_ids,
                                 confirmed_names=_tokens(names), database_path=DEFAULT_DATABASE_PATH)
        payload = preview.model_dump(mode="json", include={"import_id", "changes"},
                                     exclude={"changes": {"__all__": {"writes"}}})
        candidates_by_id = {candidate.id: candidate for candidate in extraction.candidates}
        display_names = {entity["id"]: entity["name"] for entity in preview.baseline["entities"]}
        for change in payload["changes"]:
            candidate = candidates_by_id[change["candidate_id"]]
            change.update(subject=display_names.get(candidate.subject, candidate.subject),
                          object=display_names.get(candidate.object_ref, candidate.object_ref),
                          operation=candidate.operation, value=candidate.value)
        blocked = bool(preview.errors)
        has_changes = any(change.writes for change in preview.changes)
        can_save = not blocked and has_changes
        frozen = {"preview": preview, "inputs": [text, candidates_json, selected, names]} if can_save else None
        if blocked:
            message = "Resolve the errors in the selected proposals, then preview again."
        elif not has_changes:
            message = "Selected facts are already saved. No changes to confirm."
        else:
            message = "Preview ready. Review the before and after values below, then confirm to save."
        count = sum(bool(change.writes) for change in preview.changes)
        label = f"Confirm and save {count} {'change' if count == 1 else 'changes'}"
        return payload, frozen, message, gr.update(interactive=can_save, value=label if can_save else "Confirm and Save")
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        return None, None, f"Preview failed: {exc}", gr.update(interactive=False)


def save_proposals(frozen, text: str, candidates_json: str, selected: str, names: str):
    """Save only the frozen preview when every reviewed input still matches."""
    try:
        if not frozen or frozen["inputs"] != [text, candidates_json, selected, names]:
            raise ValueError("The review changed. Preview selected changes again before saving")
        import_id = confirm_import(frozen["preview"], database_path=DEFAULT_DATABASE_PATH)
        return f"Saved import {import_id}. The shared inspector now shows the established facts.", None, gr.update(interactive=False), *refresh_world()
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        return f"Save failed: {exc}. Review and preview the changes again.", None, gr.update(interactive=False), *refresh_world()


def invalidate_preview():
    return None, None, gr.update(interactive=False, value="Confirm and Save"), "Selection or input changed. Preview selected changes again before saving."


def begin_extraction():
    return None, None, gr.update(interactive=False), "Extracting proposals. Established facts are unchanged."


def begin_extraction_ui():
    return *begin_extraction(), gr.update(interactive=False), gr.update(interactive=False)


def finish_extraction_ui():
    return gr.update(interactive=True), gr.update(interactive=True)


def cancel_review():
    return "", "", "", None, None, gr.update(interactive=False), "Review cancelled. Established facts are unchanged."


def build_app() -> gr.Blocks:
    initialize_database(DEFAULT_DATABASE_PATH)
    world = refresh_world()
    with gr.Blocks(title="Narrative Linter") as demo:
        gr.Markdown("# Narrative Linter\nBuild an evidence-backed world, then check what happens next.", elem_id="workspace-title")
        frozen_preview = gr.State(None)
        with gr.Tabs():
            with gr.Tab("World State Extraction"):
                gr.Markdown("## Review proposed world facts\nExtraction creates proposals. Only **Confirm and Save** changes the saved world.")
                gr.Markdown("**1. Select facts → 2. Preview changes → 3. Confirm and save**\n\nSelecting facts does not enable saving yet. Preview them first to check the result.")
                source = gr.Textbox(lines=8, label="Background settings or existing narrative", placeholder="Paste source text. Keep line breaks so each fact can point to its evidence.")
                extract_button = gr.Button("Extract Proposed Facts", variant="primary")
                gr.Markdown("Edit the proposed facts below and check **Accept** for each fact to save. Use saved IDs or exact names for Subject and Object. Values use JSON: `\"alive\"`, `[\"brass_key\"]`, or `null`. Keep each evidence excerpt exactly as it appears on its source line.")
                candidate_table = gr.Dataframe(
                    headers=["Accept", "ID", "Type", "Subject", "Object", "Field", "Value (JSON)", "Operation", "Certainty", "Evidence", "Line"],
                    datatype=["bool", "str", "str", "str", "str", "str", "str", "str", "str", "str", "number"],
                    type="array", value=[], interactive=True, column_count=(11, "fixed"),
                    label="Proposed facts (not saved)", wrap=True,
                )
                confirmed_names = gr.Textbox(label="Confirm new entity names", placeholder="Exact display names, separated by commas or line breaks. Required only for new characters.", lines=2)
                with gr.Accordion("Advanced proposal JSON and candidate selection", open=False):
                    candidates = gr.Code(label="Editable proposals and source evidence", language="json", interactive=True, lines=12)
                    selected_ids = gr.Textbox(label="Candidate IDs to accept", placeholder="Comma-separated IDs, or one per line.", lines=2)
                with gr.Row():
                    preview_button = gr.Button("Preview selected changes")
                    cancel_button = gr.Button("Cancel review")
                extraction_status = gr.Markdown("Paste source text to begin. Saved facts remain available in the inspector below.")
                readable_preview = gr.HTML(value=EMPTY_PREVIEW, elem_id="review-preview")
                with gr.Accordion("Technical details (JSON)", open=False):
                    final_diff = gr.JSON(label="Preview details", elem_id="review-diff")
                confirm_button = gr.Button("Confirm and Save", variant="primary", interactive=False)
            with gr.Tab("Story Consistency Check"):
                gr.Markdown("## Check the next passage\nCheck against saved world facts. Story events replay temporarily and do not update the database.")
                with gr.Row():
                    with gr.Column():
                        narrative = gr.Textbox(lines=16, label="New narrative passage")
                        audit_button = gr.Button("Run Consistency Audit", variant="primary")
                    with gr.Column():
                        report = gr.Textbox(lines=12, label="Diagnostic report", interactive=False)
                        with gr.Row():
                            verdict = gr.Textbox(label="Overall verdict", interactive=False)
                            latency = gr.Textbox(label="Execution latency", interactive=False)
                        claims_json = gr.Code(label="Extracted action claims", language="json", interactive=False)
        with gr.Group(elem_id="saved-world"):
            gr.Markdown("## Saved world inspector\nBoth modes use these established facts. Relationships and attributes are stored for review; descriptive rules are not automatically checked.")
            refresh_button = gr.Button("Refresh saved world", size="sm")
            entity_table = gr.Dataframe(headers=["ID", "Name", "Status", "Location", "Inventory", "Attributes"], value=world[0], interactive=False)
            with gr.Accordion("Locations, relationships, rules and provenance", open=False):
                with gr.Row():
                    locations = gr.JSON(value=world[1], label="Saved locations")
                    relationships = gr.JSON(value=world[2], label="Saved relationships (not automatically checked)")
                rules = gr.JSON(value=world[3], label="World rules and checking support")
                provenance = gr.JSON(value=world[4], label="Accepted changes and source provenance")
            with gr.Accordion("Manually add or edit an entity", open=False):
                with gr.Row():
                    entity_id = gr.Textbox(label="Stable ID")
                    name = gr.Textbox(label="Name")
                    status = gr.Dropdown(["Unknown", "alive", "dead", "unconscious", "injured", "missing"], label="Status", value="Unknown")
                with gr.Row():
                    location = gr.Textbox(label="Location (blank means unknown)")
                    inventory = gr.Textbox(label="Inventory JSON (null means unknown; [] means empty)", value="null")
                    attributes = gr.Textbox(label="Attributes JSON", value="{}")
                save_button = gr.Button("Add/Edit Entity")
                save_status = gr.Markdown()
        inspector_outputs = [entity_table, locations, relationships, rules, provenance]
        review_inputs = [source, candidates, selected_ids, confirmed_names]
        invalidation_outputs = [frozen_preview, final_diff, confirm_button, extraction_status]
        for component in [source, confirmed_names]:
            component.input(invalidate_preview, outputs=invalidation_outputs, queue=False)
        for component in [candidates, selected_ids]:
            component.input(sync_candidate_table, [candidates, selected_ids],
                            [candidate_table, *invalidation_outputs], queue=False)
        candidate_table.input(edit_candidate_table, candidate_table,
                              [candidates, selected_ids, *invalidation_outputs], queue=False)
        extract_button.click(begin_extraction_ui, outputs=[frozen_preview, final_diff, confirm_button, extraction_status, preview_button, extract_button], queue=False).then(
            extract_proposals, source, [candidates, extraction_status, frozen_preview, final_diff, confirm_button, selected_ids, confirmed_names]).then(
                candidate_table_rows, [candidates, selected_ids], candidate_table).then(
                finish_extraction_ui, outputs=[preview_button, extract_button])
        preview_button.click(preview_proposals, review_inputs, [final_diff, frozen_preview, extraction_status, confirm_button])
        final_diff.change(render_world_preview, final_diff, readable_preview, queue=False)
        confirm_button.click(save_proposals, [frozen_preview, *review_inputs], [extraction_status, frozen_preview, confirm_button, *inspector_outputs])
        cancel_button.click(cancel_review, outputs=[candidates, selected_ids, confirmed_names, frozen_preview, final_diff, confirm_button, extraction_status], queue=False).then(
            candidate_table_rows, [candidates, selected_ids], candidate_table)
        save_button.click(save_entity, [entity_id, name, status, location, inventory, attributes], [entity_table, save_status])
        refresh_button.click(refresh_world, outputs=inspector_outputs)
        audit_button.click(audit_narrative, narrative, [report, verdict, latency, claims_json])
    return demo


if __name__ == "__main__":
    build_app().launch(
        theme=gr.themes.Default(primary_hue="blue", secondary_hue="teal", neutral_hue="slate"),
        css=WORKSPACE_CSS,
    )
