"""Deterministic verification and sequential state replay."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .database import DEFAULT_DATABASE_PATH, list_entities, list_rules
from .extractor import Claim


class DiagnosticIssue(BaseModel):
    """A compiler-style consistency diagnostic."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["ERROR", "WARNING"]
    issue_type: Literal[
        "STATUS_VIOLATION",
        "INVENTORY_DEFICIT",
        "SPATIAL_MISMATCH",
        "RULE_BREACH",
        "ENTITY_NOT_FOUND",
    ]
    line_number: int
    flagged_text: str
    explanation: str
    evidence_source: str


ACTIVE_ACTIONS = {
    "attack", "attacked", "cast", "cast_spell", "consume", "consumed",
    "drop", "dropped", "give", "gave", "interact", "move", "moved",
    "pick_up", "picked_up", "use", "used", "kill", "killed",
}
PHYSICAL_ACTIONS = {"attack", "attacked", "hit", "strike", "interact", "touch"}
MOVE_ACTIONS = {"move", "moved", "travel", "traveled", "walk", "walked", "go", "went"}
ACQUIRE_ACTIONS = {"acquire", "acquired", "pick_up", "picked_up", "obtain", "obtained", "receive", "received"}
DROP_ACTIONS = {"drop", "dropped", "discard", "discarded"}
CONSUME_ACTIONS = {"consume", "consumed", "use", "used"}
TRANSFER_ACTIONS = {"give", "gave", "hand", "handed", "transfer", "transferred"}


def _normalized(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _same_name(left: str, right: str) -> bool:
    return _normalized(left) == _normalized(right)


def _issue(
    severity: Literal["ERROR", "WARNING"],
    issue_type: Literal[
        "STATUS_VIOLATION", "INVENTORY_DEFICIT", "SPATIAL_MISMATCH",
        "RULE_BREACH", "ENTITY_NOT_FOUND",
    ],
    claim: Claim,
    explanation: str,
    evidence: str,
) -> DiagnosticIssue:
    return DiagnosticIssue(
        severity=severity,
        issue_type=issue_type,
        line_number=claim.line_number,
        flagged_text=claim.text_span,
        explanation=explanation,
        evidence_source=evidence,
    )


def _item_candidates(claim: Claim) -> list[str]:
    return [*claim.items_used] if claim.items_used else (
        [claim.target] if claim.target and not _normalized(claim.target) in MOVE_ACTIONS else []
    )


def verify_claims(claims: list[Claim], database_path=DEFAULT_DATABASE_PATH) -> list[DiagnosticIssue]:
    """Replay claims in order and verify each claim against the evolving state."""
    state: dict[str, dict[str, Any]] = {
        _normalized(entity["name"]): {
            **entity,
            "inventory": list(entity["inventory"]),
        }
        for entity in list_entities(database_path)
    }
    rules = list_rules(database_path)
    issues: list[DiagnosticIssue] = []

    for claim in claims:
        action = _normalized(claim.action)
        subject_key = _normalized(claim.subject)
        subject = state.get(subject_key)
        if subject is None:
            issues.append(_issue(
                "WARNING", "ENTITY_NOT_FOUND", claim,
                f"Unknown subject entity '{claim.subject}'.",
                f"Database: Entity '{claim.subject}' was not found",
            ))
            continue

        target_key = _normalized(claim.target) if claim.target else None
        target = state.get(target_key) if target_key else None
        if target_key and (action in PHYSICAL_ACTIONS or action in TRANSFER_ACTIONS) and target is None:
            issues.append(_issue(
                "WARNING", "ENTITY_NOT_FOUND", claim,
                f"Unknown target entity '{claim.target}'.",
                f"Database: Entity '{claim.target}' was not found",
            ))
        if action in ACTIVE_ACTIONS and subject["status"] == "dead":
            issues.append(_issue(
                "ERROR", "STATUS_VIOLATION", claim,
                f"Dead entity '{subject['name']}' cannot perform active action '{claim.action}'.",
                f"Database: Entity '{subject['name']}' status=dead",
            ))
        if target and action in PHYSICAL_ACTIONS and target["status"] == "dead":
            issues.append(_issue(
                "ERROR", "STATUS_VIOLATION", claim,
                f"Physical interaction targets dead entity '{target['name']}'.",
                f"Database: Entity '{target['name']}' status=dead",
            ))

        items = _item_candidates(claim)
        owned = {_normalized(item): item for item in subject["inventory"]}
        missing = [item for item in items if _normalized(item) not in owned]
        if missing and action not in ACQUIRE_ACTIONS:
            issues.append(_issue(
                "ERROR", "INVENTORY_DEFICIT", claim,
                f"'{subject['name']}' does not own required item(s): {', '.join(missing)}.",
                f"Database: Entity '{subject['name']}' inventory={subject['inventory']}",
            ))

        if claim.claimed_location and not _same_name(claim.claimed_location, subject["location"]):
            if action not in MOVE_ACTIONS:
                issues.append(_issue(
                    "ERROR", "SPATIAL_MISMATCH", claim,
                    f"Claimed location differs from the subject's current location.",
                    f"Database: '{subject['name']}' location={subject['location']}; "
                    f"Narrative claimed location={claim.claimed_location}",
                ))
        if target and action in PHYSICAL_ACTIONS:
            if not _same_name(subject["location"], target["location"]):
                issues.append(_issue(
                    "ERROR", "SPATIAL_MISMATCH", claim,
                    "Physical interaction requires co-presence, but locations differ.",
                    f"Database: '{subject['name']}' location={subject['location']}; "
                    f"'{target['name']}' location={target['location']}",
                ))
            if claim.claimed_location and not _same_name(claim.claimed_location, target["location"]):
                issues.append(_issue(
                    "ERROR", "SPATIAL_MISMATCH", claim,
                    "The claimed interaction location differs from the target's current location.",
                    f"Database: '{target['name']}' location={target['location']}; "
                    f"Narrative claimed location={claim.claimed_location}",
                ))

        searchable = " ".join([action, claim.target or "", *claim.items_used]).lower()
        for rule in rules:
            forbidden = (rule["forbidden_action"] or "").lower()
            if forbidden and forbidden in searchable:
                issues.append(_issue(
                    "ERROR", "RULE_BREACH", claim, rule["description"],
                    f"Database: Rule '{rule['rule_id']}' ({rule['category']})",
                ))

        _apply_transition(state, subject, target, action, claim, items, owned)
    return issues


def _apply_transition(
    state: dict[str, dict[str, Any]],
    subject: dict[str, Any],
    target: dict[str, Any] | None,
    action: str,
    claim: Claim,
    items: list[str],
    owned: dict[str, str],
) -> None:
    """Apply a supported claim to the in-memory replay state after checking it."""
    if action in MOVE_ACTIONS:
        destination = claim.claimed_location or claim.target
        if destination:
            subject["location"] = destination
        return
    if action in ACQUIRE_ACTIONS:
        for item in items:
            if _normalized(item) not in {_normalized(value) for value in subject["inventory"]}:
                subject["inventory"].append(_normalized(item))
        return
    if action in DROP_ACTIONS or action in CONSUME_ACTIONS:
        subject["inventory"] = [
            value for value in subject["inventory"]
            if _normalized(value) not in {_normalized(item) for item in items}
        ]
        return
    if action in TRANSFER_ACTIONS and target:
        subject["inventory"] = [
            value for value in subject["inventory"]
            if _normalized(value) not in {_normalized(item) for item in items}
        ]
        target["inventory"].extend(_normalized(item) for item in items)
        return
    if action in {"kill", "killed"} and target:
        target["status"] = "dead"
