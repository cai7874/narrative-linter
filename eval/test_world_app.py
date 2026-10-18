"""Direct callback tests for the world review UI; no browser or model required."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
from core.database import initialize_database, list_accepted_changes, list_entities
from core.world_extractor import ExtractionResponseError, WorldExtractionResult
from core.world_preview import EMPTY_PREVIEW, render_world_preview


SOURCE = "Ada owns a brass key."


def proposals(certainty="explicit"):
    return WorldExtractionResult.model_validate({"candidates": [{
        "id": "c1", "fact_type": "inventory", "subject": "Ada", "object_ref": None,
        "field": None, "value": ["brass_key"], "operation": "add", "certainty": certainty,
        "text_span": SOURCE, "line_number": 1,
    }]})


class WorldAppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "world.db"
        initialize_database(self.path, seed=False)
        self.database_patch = patch.object(app, "DEFAULT_DATABASE_PATH", self.path)
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.inputs = [SOURCE, proposals().model_dump_json(indent=2), "c1", "Ada"]

    def test_app_builds_both_modes_and_shared_inspector(self):
        demo = app.build_app()
        labels = [getattr(component, "label", None) for component in demo.blocks.values()]
        self.assertIn("World State Extraction", labels)
        self.assertIn("Story Consistency Check", labels)
        self.assertIn("Saved relationships (not automatically checked)", labels)

    def test_mocked_extraction_leaves_database_untouched_and_clears_old_selection(self):
        with patch.object(app, "extract_world_facts", return_value=proposals()) as extract:
            result = app.extract_proposals(SOURCE)
        self.assertEqual(json.loads(result[0])["candidates"][0]["id"], "c1")
        self.assertEqual(result[-2:], ("", ""))
        self.assertFalse(result[4]["interactive"])
        self.assertEqual(list_entities(self.path), [])
        self.assertIn("existing_world", extract.call_args.kwargs)

    def test_extraction_populates_editable_table_with_evidence(self):
        with patch.object(app, "extract_world_facts", return_value=proposals()):
            result = app.extract_proposals(SOURCE)
        rows = app.candidate_table_rows(result[0], result[-2])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0][0])
        self.assertEqual(rows[0][1:4], ["c1", "inventory", "Ada"])
        self.assertEqual(rows[0][-2:], [SOURCE, 1])

    def test_table_edits_round_trip_and_acceptance_controls_preview(self):
        rows = app.candidate_table_rows(self.inputs[1])
        rows[0][0] = True
        rows[0][6] = '["silver_key"]'
        result = app.edit_candidate_table(rows)
        self.assertEqual(result[1], "c1")
        self.assertIsNone(result[2])
        self.assertFalse(result[4]["interactive"])
        updated = WorldExtractionResult.model_validate_json(result[0])
        self.assertEqual(updated.candidates[0].value, ["silver_key"])
        self.assertEqual(app.candidate_table_rows(result[0], result[1]), rows)

    def test_invalid_table_value_clears_canonical_input_and_reports_error(self):
        rows = app.candidate_table_rows(self.inputs[1], "c1")
        rows[0][6] = "not valid JSON"
        canonical, accepted, frozen, diff, button, status = app.edit_candidate_table(rows)
        self.assertEqual((canonical, accepted), ("", ""))
        self.assertIsNone(frozen)
        self.assertIsNone(diff)
        self.assertFalse(button["interactive"])
        self.assertIn("Row 1: Value must be valid JSON", status)
        self.assertEqual(list_entities(self.path), [])

    def test_extraction_failure_clears_preview_without_writing(self):
        with patch.object(app, "extract_world_facts", side_effect=ExtractionResponseError("invalid source")):
            result = app.extract_proposals(SOURCE)
        self.assertIn("invalid source", result[1])
        self.assertIsNone(result[2])
        self.assertFalse(result[4]["interactive"])
        self.assertEqual(list_entities(self.path), [])

    def test_extraction_disables_preview_and_repeat_extraction_until_finished(self):
        started = app.begin_extraction_ui()
        self.assertFalse(started[2]["interactive"])
        self.assertFalse(started[-2]["interactive"])
        self.assertFalse(started[-1]["interactive"])
        self.assertTrue(all(button["interactive"] for button in app.finish_extraction_ui()))

    def test_unchanged_selection_has_no_confirmation(self):
        _, frozen, _, _ = app.preview_proposals(*self.inputs)
        result = app.save_proposals(frozen, *self.inputs)
        self.assertTrue(result[0].startswith("Saved import"))
        diff, frozen, status, button = app.preview_proposals(*self.inputs)
        self.assertEqual(diff["changes"][0]["effect"], "unchanged")
        self.assertIsNone(frozen)
        self.assertFalse(button["interactive"])
        self.assertIn("No changes to confirm", status)
        self.assertIn("No change", render_world_preview(diff))

    def test_selected_uncertain_proposal_disables_save(self):
        inputs = [SOURCE, proposals("uncertain").model_dump_json(), "c1", "Ada"]
        diff, frozen, status, button = app.preview_proposals(*inputs)
        self.assertTrue(diff["changes"][0]["errors"])
        self.assertIsNone(frozen)
        self.assertFalse(button["interactive"])
        self.assertIn("Resolve", status)
        self.assertEqual(list_entities(self.path), [])

    def test_no_implicit_acceptance_or_invalid_selection(self):
        for selected in ("", "does-not-exist"):
            inputs = [*self.inputs[:2], selected, "Ada"]
            diff, frozen, _, button = app.preview_proposals(*inputs)
            self.assertIsNone(frozen)
            self.assertFalse(button["interactive"])
        self.assertEqual(list_entities(self.path), [])

    def test_edit_and_cancel_clear_frozen_preview(self):
        self.assertIsNone(app.invalidate_preview()[0])
        self.assertFalse(app.invalidate_preview()[2]["interactive"])
        cancelled = app.cancel_review()
        self.assertEqual(cancelled[:3], ("", "", ""))
        self.assertIsNone(cancelled[3])
        self.assertFalse(cancelled[5]["interactive"])
        self.assertEqual(list_entities(self.path), [])

    def test_changed_input_cannot_save_previous_preview(self):
        _, frozen, _, button = app.preview_proposals(*self.inputs)
        self.assertTrue(button["interactive"])
        changed_inputs = [self.inputs[0] + "\nChanged.", *self.inputs[1:]]
        result = app.save_proposals(frozen, *changed_inputs)
        self.assertIn("review changed", result[0])
        self.assertIsNone(result[1])
        self.assertFalse(result[2]["interactive"])
        self.assertEqual(list_entities(self.path), [])

    def test_confirm_saves_once_and_refreshes_shared_inspector(self):
        diff, frozen, _, button = app.preview_proposals(*self.inputs)
        self.assertTrue(button["interactive"])
        self.assertEqual(diff["changes"][0]["candidate_id"], "c1")
        self.assertNotIn("baseline", diff)
        self.assertNotIn("source_text", diff)
        self.assertNotIn("database_path", diff)
        self.assertNotIn("writes", diff["changes"][0])
        self.assertEqual(list_entities(self.path), [])
        result = app.save_proposals(frozen, *self.inputs)
        self.assertTrue(result[0].startswith("Saved import"), result[0])
        self.assertIsNone(result[1])
        self.assertFalse(result[2]["interactive"])
        self.assertEqual(result[3][0][1], "Ada")
        self.assertEqual(list_entities(self.path)[0]["inventory"], ["brass_key"])
        self.assertTrue(result[-1])
        count = len(list_accepted_changes(self.path))
        repeated = app.save_proposals(frozen, *self.inputs)
        self.assertTrue(repeated[0].startswith("Save failed"))
        self.assertEqual(len(list_accepted_changes(self.path)), count)

    def test_readable_preview_shows_values_evidence_and_count_without_writing(self):
        payload, _, _, button = app.preview_proposals(*self.inputs)
        html = render_world_preview(payload)
        self.assertIn("Ada · Inventory", html)
        self.assertIn("Not recorded", html)
        self.assertIn("brass_key", html)
        self.assertIn("Source · line 1", html)
        self.assertIn(SOURCE, html)
        self.assertNotIn("candidate_id", html)
        self.assertNotIn("_inventory_complete", html)
        self.assertEqual(button["value"], "Confirm and save 1 change")
        self.assertEqual(list_entities(self.path), [])

    def test_readable_preview_explains_blocked_and_stale_reviews(self):
        payload, _, _, _ = app.preview_proposals(
            SOURCE, proposals("uncertain").model_dump_json(), "c1", "Ada")
        html = render_world_preview(payload)
        self.assertIn("Needs correction", html)
        self.assertIn("preview again", html)
        self.assertEqual(render_world_preview(None), EMPTY_PREVIEW)
        self.assertIn("changed", app.invalidate_preview()[-1])

    def test_readable_preview_escapes_source_and_user_values(self):
        payload, _, _, _ = app.preview_proposals(*self.inputs)
        change = payload["changes"][0]
        change["subject"] = '<img src=x onerror="alert(1)">'
        change["text_span"] = "<script>alert(1)</script>"
        change["after"][0]["name"] = "<svg onload=alert(1)>"
        html = render_world_preview(payload)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<svg", html)
        self.assertIn("&lt;script&gt;", html)

    def test_transfer_preview_groups_both_owners_and_resolves_display_names(self):
        app.upsert_entity("ada", "Ada", "alive", "Tower", ["brass_key"], {}, self.path)
        app.upsert_entity("bea", "Bea", "alive", "Tower", [], {}, self.path)
        source = "Ada gave Bea the brass key."
        extraction = proposals()
        candidate = extraction.candidates[0]
        candidate.subject, candidate.object_ref = "ada", "bea"
        candidate.operation, candidate.certainty = "transfer", "transition"
        candidate.text_span = source
        payload, _, _, button = app.preview_proposals(source, extraction.model_dump_json(), "c1", "")
        html = render_world_preview(payload)
        self.assertTrue(button["interactive"])
        self.assertIn("Ada · Inventory → Bea", html)
        self.assertIn('class="preview-owner">Ada', html)
        self.assertIn('class="preview-owner">Bea', html)
        self.assertEqual(list_entities(self.path)[0]["inventory"], ["brass_key"])

    def test_manual_editor_preserves_unknown_and_known_empty_inventory(self):
        _, message = app.save_entity("ada", "Ada", "Unknown", "", "null", "{}")
        self.assertEqual(message, "Entity saved.")
        entity = list_entities(self.path)[0]
        self.assertIsNone(entity["status"])
        self.assertIsNone(entity["location"])
        self.assertIsNone(entity["inventory"])
        app.save_entity("ada", "Ada", "alive", "", "[]", "{}")
        self.assertEqual(list_entities(self.path)[0]["inventory"], [])


if __name__ == "__main__":
    unittest.main()
