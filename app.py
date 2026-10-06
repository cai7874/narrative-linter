"""Gradio dashboard for the narrative consistency linter."""

from __future__ import annotations

import json
import time

import gradio as gr

from core.database import DEFAULT_DATABASE_PATH, initialize_database, list_entities, upsert_entity
from core.extractor import ExtractionConfigurationError, ExtractionResponseError, extract_claims
from core.verifier import verify_claims


def _entity_rows() -> list[list[str]]:
    return [
        [
            entity["id"],
            entity["name"],
            entity["status"],
            entity["location"],
            json.dumps(entity["inventory"]),
            json.dumps(entity["attributes"]),
        ]
        for entity in list_entities(DEFAULT_DATABASE_PATH)
    ]


def refresh_entities() -> list[list[str]]:
    return _entity_rows()


def save_entity(entity_id: str, name: str, status: str, location: str, inventory: str, attributes: str):
    try:
        parsed_inventory = json.loads(inventory or "[]")
        parsed_attributes = json.loads(attributes or "{}")
        if not isinstance(parsed_inventory, list) or not isinstance(parsed_attributes, dict):
            raise ValueError("Inventory must be a JSON array and attributes must be a JSON object")
        upsert_entity(entity_id, name, status, location, parsed_inventory, parsed_attributes, DEFAULT_DATABASE_PATH)
        return _entity_rows(), "Entity saved."
    except (ValueError, json.JSONDecodeError) as exc:
        return _entity_rows(), f"Save failed: {exc}"


def audit_narrative(text: str):
    started = time.perf_counter()
    try:
        extraction = extract_claims(text)
        issues = verify_claims(extraction.claims)
        if not issues:
            return "PASSED\n\nNo consistency issues detected.", "PASSED", _latency(started)
        report = "\n\n".join(
            f"[{issue.severity}] line {issue.line_number} {issue.issue_type}\n"
            f"  {issue.flagged_text}\n"
            f"  {issue.explanation}\n"
            f"  Evidence: {issue.evidence_source}"
            for issue in issues
        )
        return report, "CONFLICT DETECTED", _latency(started)
    except (ExtractionConfigurationError, ExtractionResponseError, ValueError) as exc:
        return f"[ERROR] {exc}", "AUDIT FAILED", _latency(started)


def _latency(started: float) -> str:
    return f"{(time.perf_counter() - started) * 1000:.2f} ms"


def build_app() -> gr.Blocks:
    initialize_database(DEFAULT_DATABASE_PATH)
    with gr.Blocks(title="Narrative Linter") as demo:
        gr.Markdown("# Narrative Linter\nDeterministic consistency auditing against a SQLite world model.")
        with gr.Row():
            with gr.Column():
                gr.Markdown("## World State Inspector")
                entity_table = gr.Dataframe(
                    headers=["ID", "Name", "Status", "Location", "Inventory", "Attributes"],
                    value=_entity_rows(),
                    interactive=False,
                )
                with gr.Row():
                    entity_id = gr.Textbox(label="ID")
                    name = gr.Textbox(label="Name")
                    status = gr.Dropdown(
                        ["alive", "dead", "unconscious", "injured", "missing"],
                        label="Status",
                        value="alive",
                    )
                with gr.Row():
                    location = gr.Textbox(label="Location")
                    inventory = gr.Textbox(label="Inventory JSON", value="[]")
                    attributes = gr.Textbox(label="Attributes JSON", value="{}")
                with gr.Row():
                    save_button = gr.Button("Add/Edit Entity")
                    refresh_button = gr.Button("Refresh")
                save_status = gr.Markdown()
                gr.Markdown("## Narrative Input")
                narrative = gr.Textbox(lines=12, label="Narrative excerpt")
                audit_button = gr.Button("Run Consistency Audit", variant="primary")
            with gr.Column():
                gr.Markdown("## Diagnostic Report Console")
                report = gr.Textbox(lines=24, label="Compiler diagnostics", interactive=False)
                verdict = gr.Textbox(label="Overall verdict", interactive=False)
                latency = gr.Textbox(label="Execution latency", interactive=False)
        save_button.click(
            save_entity,
            [entity_id, name, status, location, inventory, attributes],
            [entity_table, save_status],
        )
        refresh_button.click(refresh_entities, outputs=entity_table)
        audit_button.click(audit_narrative, narrative, [report, verdict, latency])
    return demo


if __name__ == "__main__":
    build_app().launch()
