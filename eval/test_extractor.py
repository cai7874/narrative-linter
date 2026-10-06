"""Extractor contract tests."""

import os
from types import SimpleNamespace
import unittest

from core.extractor import ExtractionResponseError, ExtractionResult, extract_claims


class ExtractorTests(unittest.TestCase):
    def test_schema_contract(self):
        result = ExtractionResult.model_validate(
            {
                "claims": [
                    {
                        "subject": "Hero",
                        "action": "attacked",
                        "target": "Guard",
                        "items_used": ["silver_dagger"],
                        "claimed_location": "Castle Courtyard",
                        "text_span": "Hero attacked Guard.",
                        "line_number": 1,
                    }
                ]
            }
        )
        self.assertEqual(result.claims[0].line_number, 1)

    def test_lm_studio_defaults_are_available(self):
        saved = {key: os.environ.pop(key, None) for key in (
            "NARRATIVE_LINTER_API_KEY",
            "OPENAI_API_KEY",
            "NARRATIVE_LINTER_BASE_URL",
            "OPENAI_BASE_URL",
            "NARRATIVE_LINTER_MODEL",
        )}
        try:
            from core.extractor import _client_settings

            self.assertEqual(
                _client_settings(),
                ("lm-studio", "qwen/qwen3.5-9b", "http://127.0.0.1:1234/v1"),
            )
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_user_can_override_lm_studio_defaults(self):
        saved = {
            key: os.environ.get(key)
            for key in (
                "NARRATIVE_LINTER_API_KEY",
                "NARRATIVE_LINTER_BASE_URL",
                "NARRATIVE_LINTER_MODEL",
            )
        }
        try:
            os.environ["NARRATIVE_LINTER_API_KEY"] = "custom-key"
            os.environ["NARRATIVE_LINTER_BASE_URL"] = "http://localhost:9000/v1"
            os.environ["NARRATIVE_LINTER_MODEL"] = "custom-model"
            from core.extractor import _client_settings

            self.assertEqual(
                _client_settings(),
                ("custom-key", "custom-model", "http://localhost:9000/v1"),
            )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_accepts_lm_studio_reasoning_content(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        reasoning_content=(
                            '{"claims":[{"subject":"Hero","action":"moved",'
                            '"target":null,"items_used":[],"claimed_location":"Castle Courtyard",'
                            '"text_span":"Hero moved.","line_number":1}]}'
                        ),
                    )
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )
        result = extract_claims("Hero moved.", client=client)
        self.assertEqual(result.claims[0].subject, "Hero")

    def test_rejects_non_object_response(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="[]", reasoning_content=None))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
        )
        with self.assertRaises(ExtractionResponseError):
            extract_claims("Hero moved.", client=client)


if __name__ == "__main__":
    unittest.main()
