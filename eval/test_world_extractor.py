"""Offline tests for world extraction schema and source evidence validation."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import APIConnectionError
from pydantic import ValidationError

from core.world_extractor import (
    ExtractionResponseError, WorldExtractionResult, extract_world_facts,
)


def fact(**overrides):
    candidate = dict(id="c1", fact_type="inventory", subject="Ada", object_ref=None,
                     field=None, value=["brass_key"], operation="add", certainty="explicit",
                     text_span="Ada owns a brass key.", line_number=1)
    candidate.update(overrides)
    return candidate


class FakeClient:
    def __init__(self, content=None, *, reasoning=None, error=None):
        self.content = content
        self.reasoning = reasoning
        self.error = error
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=self.content, reasoning_content=self.reasoning,
        ))])


class WorldExtractorTests(unittest.TestCase):
    def client(self, *candidates):
        return FakeClient(json.dumps({"candidates": list(candidates)}))

    def test_evidenced_candidate_and_context_use_dedicated_schema(self):
        client = self.client(fact())
        result = extract_world_facts("Ada owns a brass key.", {"entities": []}, client)
        self.assertEqual(result.candidates[0].value, ["brass_key"])
        request = client.calls[0]
        self.assertEqual(request["response_format"]["json_schema"]["name"], "world_state_candidates")
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertEqual(json.loads(request["messages"][1]["content"])["existing_world"], {"entities": []})
        self.assertEqual(json.loads(request["messages"][1]["content"])["source_lines"], [
            {"line_number": 1, "text": "Ada owns a brass key."},
        ])

    def test_default_world_client_has_bounded_timeout_without_retries(self):
        client = self.client(fact())
        with patch("core.world_extractor.OpenAI", return_value=client) as factory:
            extract_world_facts("Ada owns a brass key.")
        self.assertEqual(factory.call_args.kwargs["timeout"], 180)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)

    def test_unknown_fields_remain_null(self):
        result = extract_world_facts("Ada exists.", client=self.client(fact(
            fact_type="entity", value=None, text_span="Ada exists.",
        )))
        self.assertIsNone(result.candidates[0].value)
        self.assertIsNone(result.candidates[0].object_ref)

    def test_negation_and_uncertainty_are_preserved_for_review(self):
        for certainty in ("negated", "uncertain", "unresolved"):
            result = extract_world_facts("Ada owns a brass key.", client=self.client(fact(certainty=certainty)))
            self.assertEqual(result.candidates[0].certainty, certainty)

    def test_transfer_retains_direction_and_order(self):
        source = "Ada owns a brass key.\nAda gives the key to Ben."
        result = extract_world_facts(source, client=self.client(fact(), fact(
            id="c2", object_ref="Ben", operation="transfer", certainty="transition",
            line_number=2, text_span="Ada gives the key to Ben.",
        )))
        self.assertEqual(result.candidates[1].object_ref, "Ben")
        self.assertEqual(result.candidates[1].operation, "transfer")

    def test_rejects_invalid_evidence_or_order_or_duplicate_ids(self):
        cases = [
            [fact(text_span="Ada owns a sword.")],
            [fact(line_number=3)],
            [fact(text_span=" ")],
            [fact(), fact()],
            [fact(line_number=2), fact(id="c2", line_number=1)],
        ]
        for candidates in cases:
            with self.subTest(candidates=candidates), self.assertRaises(ExtractionResponseError):
                extract_world_facts("Ada owns a brass key.\nAda owns a brass key.", client=self.client(*candidates))

    def test_rejects_malformed_output(self):
        for content in ("not JSON", "[]", "{}", '{"candidates":null}',
                        json.dumps({"candidates": [fact(line_number="1")]}),
                        json.dumps({"candidates": [fact(sql="DELETE FROM entities")]})):
            with self.subTest(content=content), self.assertRaises(ExtractionResponseError):
                extract_world_facts("Ada owns a brass key.", client=FakeClient(content))

    def test_unknown_fact_types_and_operations_are_rejected(self):
        for candidate in (fact(fact_type="sql"), fact(operation="execute"), fact(certainty="certain")):
            with self.assertRaises(ValidationError):
                WorldExtractionResult(candidates=[candidate])

    def test_supports_fenced_json_and_local_reasoning_fallback(self):
        content = '```json\n' + json.dumps({"candidates": [fact()]}) + '\n```'
        result = extract_world_facts("Ada owns a brass key.", client=FakeClient("", reasoning=content))
        self.assertEqual(len(result.candidates), 1)

    def test_empty_response_and_provider_failure_have_readable_errors(self):
        with self.assertRaisesRegex(ExtractionResponseError, "empty response"):
            extract_world_facts("Ada exists.", client=FakeClient())
        error = APIConnectionError(request=httpx.Request("POST", "http://localhost/v1"))
        with self.assertRaisesRegex(ExtractionResponseError, "request failed"):
            extract_world_facts("Ada exists.", client=FakeClient(error=error))

    def test_empty_source_does_not_call_provider(self):
        client = FakeClient()
        with self.assertRaises(ValueError):
            extract_world_facts(" \n", client=client)
        self.assertEqual(client.calls, [])

    def test_live_evaluation_rejects_wrong_fact_type_and_unsafe_negation(self):
        from eval.live_world_extraction import evaluate

        checks = evaluate("explicit_settings", [fact(fact_type="entity", field="inventory")])
        self.assertFalse(checks["entity_rows_are_identity_only"])
        self.assertFalse(checks["partial_key_possession"])
        checks = evaluate("negation", [fact(subject="Ivo", value=[], operation="set")])
        self.assertFalse(checks["negation_retained"])
        self.assertFalse(checks["no_positive_or_empty_inventory"])

    def test_live_evaluation_checks_uncertainty_independently_of_status_values(self):
        from eval.live_world_extraction import evaluate

        candidates = [fact(fact_type="status", subject="Mira", value=value) for value in ("dead", "alive")]
        checks = evaluate("contradiction", candidates)
        self.assertTrue(checks["alternatives_preserved"])
        self.assertFalse(checks["alternatives_require_review"])
        for candidate in candidates:
            candidate["certainty"] = "uncertain"
        self.assertTrue(all(evaluate("contradiction", candidates).values()))


if __name__ == "__main__":
    unittest.main()
