"""Verifier behavior tests."""

import tempfile
import unittest
from pathlib import Path

from core.database import initialize_database
from core.extractor import Claim
from core.verifier import verify_claims


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "world.db"
        initialize_database(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_detects_status_inventory_spatial_and_rule_breaches(self):
        issues = verify_claims(
            [
                Claim(
                    subject="Mentor",
                    action="attacked",
                    target="Guard",
                    items_used=["silver_dagger"],
                    text_span="Mentor attacked Guard with the silver dagger.",
                    line_number=4,
                ),
                Claim(
                    subject="Hero",
                    action="teleport",
                    text_span="Hero teleported.",
                    line_number=5,
                ),
            ],
            self.path,
        )
        issue_types = {issue.issue_type for issue in issues}
        self.assertIn("STATUS_VIOLATION", issue_types)
        self.assertIn("INVENTORY_DEFICIT", issue_types)
        self.assertIn("SPATIAL_MISMATCH", issue_types)
        self.assertIn("RULE_BREACH", issue_types)

    def test_passes_valid_co_present_action(self):
        claim = Claim(
            subject="Hero",
            action="attacked",
            target="Guard",
            items_used=["silver_dagger"],
            text_span="Hero attacked Guard with the silver dagger.",
            line_number=1,
        )
        self.assertEqual(verify_claims([claim], self.path), [])

    def test_normalizes_item_spaces_and_underscores(self):
        claim = Claim(
            subject="Hero",
            action="attacked",
            target="Guard",
            items_used=["silver dagger"],
            text_span="Hero attacked Guard with the silver dagger.",
            line_number=1,
        )
        self.assertEqual(verify_claims([claim], self.path), [])

    def test_validates_claimed_location(self):
        claim = Claim(
            subject="Hero",
            action="attacked",
            target="Guard",
            items_used=["silver_dagger"],
            claimed_location="Castle Crypt",
            text_span="Hero attacked Guard in the Castle Crypt.",
            line_number=1,
        )
        self.assertIn("SPATIAL_MISMATCH", {issue.issue_type for issue in verify_claims([claim], self.path)})

    def test_replays_movement_for_following_claims(self):
        claims = [
            Claim(
                subject="Hero",
                action="moved",
                claimed_location="Castle Crypt",
                text_span="Hero moved to the Castle Crypt.",
                line_number=1,
            ),
            Claim(
                subject="Hero",
                action="attacked",
                target="Guard",
                items_used=["silver_dagger"],
                claimed_location="Castle Crypt",
                text_span="Hero attacked Guard in the Castle Crypt.",
                line_number=2,
            ),
        ]
        self.assertIn("SPATIAL_MISMATCH", {issue.issue_type for issue in verify_claims(claims, self.path)})

    def test_replays_inventory_transfer_and_consumption(self):
        claims = [
            Claim(
                subject="Hero",
                action="gave",
                target="Guard",
                items_used=["silver_dagger"],
                text_span="Hero gave Guard the silver dagger.",
                line_number=1,
            ),
            Claim(
                subject="Hero",
                action="attacked",
                target="Guard",
                items_used=["silver_dagger"],
                text_span="Hero attacked Guard with the silver dagger.",
                line_number=2,
            ),
        ]
        self.assertIn("INVENTORY_DEFICIT", {issue.issue_type for issue in verify_claims(claims, self.path)})

    def test_unknown_entities_have_dedicated_diagnostic(self):
        claim = Claim(
            subject="Aria",
            action="attacked",
            target="Guard",
            text_span="Aria attacked Guard.",
            line_number=1,
        )
        self.assertEqual(verify_claims([claim], self.path)[0].issue_type, "ENTITY_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
