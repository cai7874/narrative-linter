"""Regression cases for concrete false positives, missed checks, and replay errors."""
import tempfile
import unittest
from pathlib import Path
from core.audit import audit_claims
from core.database import initialize_database, list_entities
from core.extractor import Claim, ExtractionResult, ExtractionResponseError, validate_source_spans
from core.verifier import verify_claims


def claim(action, subject="Hero", target=None, items=(), location=None, line=1):
    return Claim(subject=subject, action=action, target=target, items_used=list(items),
                 claimed_location=location, text_span=f"{subject} {action}.", line_number=line)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "world.db"
        initialize_database(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_bare_attack_does_not_require_target_as_item(self):
        self.assertEqual(verify_claims([claim("attacked", target="Guard")], self.path), [])

    def test_dead_subject_cannot_use_movement_aliases(self):
        for alias in ("walked", "travelled", "went", "moved"):
            with self.subTest(alias=alias):
                issues = verify_claims([claim(alias, subject="Mentor", location="Forest")], self.path)
                self.assertIn("STATUS_VIOLATION", {i.issue_type for i in issues})

    def test_invalid_transfer_cannot_create_an_item(self):
        events = [claim("gave", target="Guard", items=["magic_wand"]),
                  claim("used", subject="Guard", items=["magic_wand"], line=2)]
        deficits = [i.line_number for i in verify_claims(events, self.path) if i.issue_type == "INVENTORY_DEFICIT"]
        self.assertEqual(deficits, [1, 2])

    def test_failed_movement_does_not_change_location(self):
        events = [claim("walked", subject="Mentor", location="Castle Courtyard"),
                  claim("attacked", target="Mentor", line=2)]
        self.assertIn("SPATIAL_MISMATCH", {i.issue_type for i in verify_claims(events, self.path) if i.line_number == 2})

    def test_unsupported_action_is_visible(self):
        result = audit_claims([claim("danced")], self.path)
        self.assertEqual(result["verdict"], "PARTIAL CHECK")
        self.assertEqual(result["issues"][0]["issue_type"], "UNSUPPORTED_ACTION")

    def test_no_events_are_not_checked(self):
        self.assertEqual(audit_claims([], self.path)["verdict"], "NOT CHECKED")

    def test_use_does_not_consume_but_consume_does(self):
        events = [claim("used", items=["torch"]), claim("used", items=["torch"], line=2),
                  claim("consumed", items=["torch"], line=3), claim("used", items=["torch"], line=4)]
        self.assertEqual([i.line_number for i in verify_claims(events, self.path)], [4])

    def test_transfer_requires_co_presence_and_does_not_remove_item_on_failure(self):
        events = [claim("moved", subject="Guard", location="Forest"),
                  claim("gave", target="Guard", items=["torch"], line=2),
                  claim("used", items=["torch"], line=3)]
        issues = verify_claims(events, self.path)
        self.assertTrue(any(i.line_number == 2 and i.issue_type == "SPATIAL_MISMATCH" for i in issues))
        self.assertFalse(any(i.line_number == 3 for i in issues))

    def test_replay_does_not_persist_in_sqlite(self):
        before = list_entities(self.path)
        verify_claims([claim("moved", location="Forest"), claim("consumed", items=["torch"], line=2)], self.path)
        self.assertEqual(list_entities(self.path), before)

    def test_missing_destination_is_visible(self):
        self.assertEqual(verify_claims([claim("walked")], self.path)[0].issue_type, "INCOMPLETE_CLAIM")

    def test_unknown_action_still_reports_forbidden_rule(self):
        types = {i.issue_type for i in verify_claims([claim("perform_blood_magic")], self.path)}
        self.assertEqual(types, {"RULE_BREACH", "UNSUPPORTED_ACTION"})

    def test_killing_updates_status_only_after_valid_co_present_event(self):
        events = [claim("killed", target="Guard"), claim("walked", subject="Guard", location="Forest", line=2)]
        self.assertIn("STATUS_VIOLATION", {i.issue_type for i in verify_claims(events, self.path) if i.line_number == 2})

    def test_source_evidence_must_match_line(self):
        extraction = ExtractionResult(claims=[claim("walked")])
        with self.assertRaises(ExtractionResponseError):
            validate_source_spans(extraction, "Hero danced.")
        with self.assertRaises(ExtractionResponseError):
            validate_source_spans(ExtractionResult(claims=[claim("walked", line=2)]), "Hero walked.")
        validate_source_spans(extraction, "Hero walked.")

    def test_source_events_must_follow_line_order(self):
        events = [claim("walked", line=2), claim("used", line=1)]
        with self.assertRaises(ExtractionResponseError):
            validate_source_spans(ExtractionResult(claims=events), "Hero used.\nHero walked.")


if __name__ == "__main__":
    unittest.main()
