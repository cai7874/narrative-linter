"""Deterministic checks with conservative, request-scoped state replay."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict
from .database import DEFAULT_DATABASE_PATH, list_entities, list_rules
from .extractor import Claim


class DiagnosticIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["ERROR", "WARNING"]
    issue_type: Literal[
        "STATUS_VIOLATION", "INVENTORY_DEFICIT", "SPATIAL_MISMATCH",
        "RULE_BREACH", "ENTITY_NOT_FOUND", "UNSUPPORTED_ACTION", "INCOMPLETE_CLAIM",
    ]
    line_number: int
    flagged_text: str
    explanation: str
    evidence_source: str


def _normalized(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


# One vocabulary governs status checks, supported actions, and transitions.
ACTION_ALIASES = {
    "attack": ("attack", "attacked", "hit", "strike", "struck"),
    "interact": ("interact", "interacted", "touch", "touched"),
    "move": ("move", "moved", "travel", "traveled", "travelled", "walk", "walked", "go", "went"),
    "pick_up": ("acquire", "acquired", "pick_up", "picked_up", "obtain", "obtained", "receive", "received"),
    "drop": ("drop", "dropped", "discard", "discarded"),
    "consume": ("consume", "consumed"),
    "use": ("use", "used"),
    "give": ("give", "gave", "hand", "handed", "transfer", "transferred"),
    "kill": ("kill", "killed"),
    "cast": ("cast", "cast_spell"),
    "teleport": ("teleport", "teleported"),
    "blood_magic": ("blood_magic", "cast_blood_magic"),
}
ACTION_LOOKUP = {alias: action for action, aliases in ACTION_ALIASES.items() for alias in aliases}
PHYSICAL_ACTIONS = {"attack", "interact", "give", "kill"}
ITEM_TARGET_ACTIONS = {"pick_up", "drop", "consume", "use"}


def _same_name(left: str, right: str) -> bool:
    return _normalized(left) == _normalized(right)


def _item_candidates(claim: Claim, action: str) -> list[str]:
    if claim.items_used:
        return list(dict.fromkeys(_normalized(item) for item in claim.items_used))
    if action in ITEM_TARGET_ACTIONS and claim.target:
        return [_normalized(claim.target)]
    return []


def verify_claims(claims: list[Claim], database_path=DEFAULT_DATABASE_PATH) -> list[DiagnosticIssue]:
    """Check each event; only events without errors or warnings advance state.

    Input order is the event order. Unknown subjects/actions and incomplete claims
    are warnings, not silent passes. The SQLite seed is never changed by replay.
    """
    state: dict[str, dict[str, Any]] = {
        _normalized(entity["name"]): {**entity, "inventory": list(entity["inventory"])}
        for entity in list_entities(database_path)
    }
    rules = list_rules(database_path)
    issues: list[DiagnosticIssue] = []
    for claim in claims:
        event_issues: list[DiagnosticIssue] = []

        def issue(severity, kind, explanation, evidence):
            event_issues.append(DiagnosticIssue(
                severity=severity, issue_type=kind, line_number=claim.line_number,
                flagged_text=claim.text_span, explanation=explanation, evidence_source=evidence,
            ))

        raw_action = _normalized(claim.action)
        action = ACTION_LOOKUP.get(raw_action)
        subject = state.get(_normalized(claim.subject))
        if subject is None:
            issue("WARNING", "ENTITY_NOT_FOUND", f"Unknown subject '{claim.subject}'.", "World state: subject not found")
        if action is None:
            issue("WARNING", "UNSUPPORTED_ACTION", f"Action '{claim.action}' is outside the implemented vocabulary.", "Verifier: ACTION_ALIASES")
        searchable = " ".join([raw_action, claim.target or "", *claim.items_used])
        for rule in rules:
            forbidden = _normalized(rule["forbidden_action"] or "")
            if forbidden and forbidden in _normalized(searchable):
                issue("ERROR", "RULE_BREACH", rule["description"], f"World rule: {rule['rule_id']} ({rule['category']})")
        if subject is None or action is None:
            issues.extend(event_issues)
            continue

        target = state.get(_normalized(claim.target)) if claim.target else None
        if action in PHYSICAL_ACTIONS:
            if not claim.target:
                issue("WARNING", "INCOMPLETE_CLAIM", "This action requires an explicit target entity.", "Verifier: physical/transfer action requirements")
            elif target is None:
                issue("WARNING", "ENTITY_NOT_FOUND", f"Unknown target '{claim.target}'.", "World state: target not found")
        if subject["status"] == "dead":
            issue("ERROR", "STATUS_VIOLATION", f"Dead entity '{subject['name']}' cannot perform '{claim.action}'.", f"Replay state: {subject['name']} status=dead")
        if target and action in {"attack", "interact", "kill"} and target["status"] == "dead":
            issue("ERROR", "STATUS_VIOLATION", f"This action targets dead entity '{target['name']}'.", f"Replay state: {target['name']} status=dead")

        items = _item_candidates(claim, action)
        owned = {_normalized(item) for item in subject["inventory"]}
        if action in {"pick_up", "drop", "consume", "use", "give"} and not items:
            issue("WARNING", "INCOMPLETE_CLAIM", "This action requires an explicit item.", "Verifier: item action requirements")
        missing = [item for item in items if item not in owned]
        if missing and action != "pick_up":
            issue("ERROR", "INVENTORY_DEFICIT", f"'{subject['name']}' does not own required item(s): {', '.join(missing)}.", f"Replay state: inventory={subject['inventory']}")
        if action == "move" and not (claim.claimed_location or claim.target):
            issue("WARNING", "INCOMPLETE_CLAIM", "Movement requires a destination.", "Verifier: movement requirements")
        if claim.claimed_location and action != "move" and not _same_name(claim.claimed_location, subject["location"]):
            issue("ERROR", "SPATIAL_MISMATCH", "Claimed location differs from the subject's current location.", f"Replay state: {subject['name']} location={subject['location']}; claimed={claim.claimed_location}")
        if target and action in PHYSICAL_ACTIONS:
            if not _same_name(subject["location"], target["location"]):
                issue("ERROR", "SPATIAL_MISMATCH", "Physical interaction/transfer requires co-presence.", f"Replay state: {subject['name']} location={subject['location']}; {target['name']} location={target['location']}")
            if claim.claimed_location and not _same_name(claim.claimed_location, target["location"]):
                issue("ERROR", "SPATIAL_MISMATCH", "Claimed interaction location differs from the target location.", f"Replay state: {target['name']} location={target['location']}; claimed={claim.claimed_location}")

        issues.extend(event_issues)
        if not event_issues:
            _apply_transition(subject, target, action, claim, items)
    return issues


def _apply_transition(subject, target, action, claim, items):
    if action == "move":
        subject["location"] = claim.claimed_location or claim.target
    elif action == "pick_up":
        owned = {_normalized(value) for value in subject["inventory"]}
        subject["inventory"].extend(item for item in items if item not in owned)
    elif action in {"drop", "consume", "give"}:
        subject["inventory"] = [value for value in subject["inventory"] if _normalized(value) not in items]
        if action == "give":
            owned = {_normalized(value) for value in target["inventory"]}
            target["inventory"].extend(item for item in items if item not in owned)
    elif action == "kill":
        target["status"] = "dead"
    # Ordinary 'use' does not imply consumption. Quantities are not modeled.
