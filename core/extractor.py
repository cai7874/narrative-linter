"""Structured narrative claim extraction via an OpenAI-compatible API."""

from __future__ import annotations

import json
import os

from openai import APIError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Claim(BaseModel):
    """A discrete action claim extracted from narrative text."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target: str | None = None
    items_used: list[str] = Field(default_factory=list)
    claimed_location: str | None = None
    text_span: str = Field(min_length=1)
    line_number: int = Field(ge=1)


class ExtractionResult(BaseModel):
    """Strict extraction response."""

    model_config = ConfigDict(extra="forbid")

    claims: list[Claim]


SYSTEM_PROMPT = """You extract discrete action claims from narrative text.
Return only JSON matching the provided schema.
Preserve the exact complete sentence or clause in text_span and use 1-based input line numbers.
Return claims in narrative order. Each text_span must be an exact substring of its stated line.
Use null for unknown targets or locations.
Do not infer actions that are not stated or clearly entailed.
Extract every explicitly used item, weapon, or tool into items_used.
Normalize item names to lowercase snake_case, for example "the silver dagger" becomes "silver_dagger".
Extract an explicit location phrase into claimed_location.

Example:
Narrative: Hero attacked Guard with the silver dagger in the Castle Courtyard.
JSON claim: {"subject":"Hero","action":"attacked","target":"Guard","items_used":["silver_dagger"],"claimed_location":"Castle Courtyard","text_span":"Hero attacked Guard with the silver dagger in the Castle Courtyard.","line_number":1}"""


class ExtractionConfigurationError(RuntimeError):
    """Raised when the OpenAI-compatible client is not configured."""


class ExtractionResponseError(RuntimeError):
    """Raised when the model response is invalid."""


def _client_settings() -> tuple[str, str, str | None]:
    api_key = (
        os.getenv("NARRATIVE_LINTER_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or "lm-studio"
    )
    base_url = (
        os.getenv("NARRATIVE_LINTER_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "http://127.0.0.1:1234/v1"
    )
    model = os.getenv("NARRATIVE_LINTER_MODEL") or "qwen/qwen3.5-9b"
    return api_key, model, base_url


def extract_claims(text: str, client: OpenAI | None = None) -> ExtractionResult:
    """Extract claims using strict JSON schema validation."""
    if not text.strip():
        raise ValueError("Narrative text cannot be empty")
    api_key, model, base_url = _client_settings()
    active_client = client or OpenAI(api_key=api_key, base_url=base_url)
    schema = ExtractionResult.model_json_schema()
    try:
        response = active_client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "narrative_claims", "strict": True, "schema": schema},
            },
        )
        message = response.choices[0].message
        content = message.content or getattr(message, "reasoning_content", None)
        if not content:
            raise ExtractionResponseError(
                "The extraction model returned neither content nor reasoning_content"
            )
        result = ExtractionResult.model_validate(_parse_json_content(content))
        validate_source_spans(result, text)
        return result
    except (json.JSONDecodeError, ValidationError, IndexError, AttributeError, TypeError) as exc:
        raise ExtractionResponseError(f"Invalid structured extraction response: {exc}") from exc
    except APIError as exc:
        raise ExtractionResponseError(f"LLM request failed: {exc}") from exc


def _parse_json_content(content: str) -> dict:
    """Parse a JSON object while allowing a single markdown fence from local models."""
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise TypeError("The extraction response must be a JSON object")
    return payload


def validate_source_spans(result: ExtractionResult, text: str) -> None:
    """Require every extracted span to occur on its stated source line.

    This validates traceability, not that the action is semantically entailed.
    Multi-line clauses are not supported by the current claim format.
    """
    lines = text.splitlines()
    previous_line = 0
    for claim in result.claims:
        if claim.line_number > len(lines):
            raise ExtractionResponseError("Extracted line_number is outside the input")
        if claim.line_number < previous_line:
            raise ExtractionResponseError("Extracted events are not in source-line order")
        if claim.text_span not in lines[claim.line_number - 1]:
            raise ExtractionResponseError("Extracted text_span does not occur on its stated line")
        previous_line = claim.line_number
