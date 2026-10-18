"""Opt-in annotated live-model evaluation; never imports facts into SQLite.

Run: python -m eval.live_world_extraction --run
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from core.extractor import _client_settings
from core.world_extractor import ExtractionResponseError, extract_world_facts


CASES = [
    {
        "id": "explicit_settings",
        "source": "Mira is alive in North Tower. Mira owns a brass key.",
        "context": {},
        "annotation": "Mira has status alive, location North Tower, and brass_key possession. Inventory is partial.",
        "correction": "Add any omitted status/location/possession and change incorrectly typed entity rows to their actual fact types.",
    },
    {
        "id": "transfer",
        "source": "Mira owns a brass key.\nMira gave the brass key to Ivo.",
        "context": {},
        "annotation": "One explicit inventory add for Mira, followed by one directed transition transfer from Mira to Ivo.",
        "correction": "Replace any simultaneous ownership assertions with a directed inventory transfer from Mira to Ivo.",
    },
    {
        "id": "omissions",
        "source": "A character named Sera exists.",
        "context": {},
        "annotation": "Only Sera's identity is known; do not create alive status, location, or an empty inventory.",
        "correction": "Remove invented status/location/inventory facts; retain only the stated name Sera.",
    },
    {
        "id": "negation",
        "source": "Ivo does not own a sword.",
        "context": {},
        "annotation": "A negated sword ownership claim requiring review, never positive ownership or a complete empty inventory.",
        "correction": "Mark the sword claim negated and remove positive ownership or invented empty-inventory replacements.",
    },
    {
        "id": "ambiguous_reference",
        "source": "The captain is in Harbor. He owns a map.",
        "context": {"entities": [
            {"id": "captain_ada", "name": "Ada", "attributes": {"title": "captain"}},
            {"id": "captain_ben", "name": "Ben", "attributes": {"title": "captain"}},
        ]},
        "annotation": "Captain and He cannot be resolved to Ada or Ben; location and map claims require unresolved review.",
        "correction": "Resolve the captain/pronoun against Ada and Ben explicitly; until then mark affected candidates unresolved.",
    },
    {
        "id": "contradiction",
        "source": "Mira is dead. Mira is alive.",
        "context": {},
        "annotation": "Preserve both incompatible statuses as uncertain; no stated transition chooses a final status.",
        "correction": "Retain both dead and alive status alternatives as uncertain; a person must select or explain the final state.",
    },
]


def _has_item(value, item):
    return value == item or isinstance(value, list) and item in value


def evaluate(case_id, candidates):
    """Return named semantic predicates rather than treating valid JSON as quality."""
    facts = [c for c in candidates if c["fact_type"] != "entity"]
    valid_entity_rows = all(c["field"] == "name" and c["value"] == c["subject"]
                            for c in candidates if c["fact_type"] == "entity")
    checks = {"entity_rows_are_identity_only": valid_entity_rows}
    if case_id == "explicit_settings":
        checks.update(
            alive_status=any(c["fact_type"] == "status" and c["subject"] == "Mira" and c["value"] == "alive" and c["certainty"] == "explicit" for c in facts),
            tower_location=any(c["fact_type"] == "location" and c["subject"] == "Mira" and c["value"] == "North Tower" for c in facts),
            partial_key_possession=any(c["fact_type"] == "inventory" and c["subject"] == "Mira" and _has_item(c["value"], "brass_key") and c["operation"] == "add" for c in facts),
        )
    elif case_id == "transfer":
        transfers = [c for c in facts if c["operation"] == "transfer"]
        checks.update(
            initial_key_possession=any(c["fact_type"] == "inventory" and c["subject"] == "Mira" and c["operation"] == "add" and c["line_number"] == 1 and _has_item(c["value"], "brass_key") for c in facts),
            directed_transfer=len(transfers) == 1 and transfers[0]["fact_type"] == "inventory" and transfers[0]["subject"] == "Mira" and transfers[0]["object_ref"] == "Ivo" and transfers[0]["certainty"] == "transition" and _has_item(transfers[0]["value"], "brass_key"),
            no_duplicate_transfer_ownership=not any(c["line_number"] == 2 and c["fact_type"] == "inventory" and c["operation"] != "transfer" for c in facts),
        )
    elif case_id == "omissions":
        checks.update(
            stated_identity=any(c["fact_type"] == "entity" and c["subject"] == "Sera" and c["value"] == "Sera" for c in candidates),
            no_invented_fields=not facts,
        )
    elif case_id == "negation":
        inventory = [c for c in facts if c["fact_type"] == "inventory"]
        checks.update(
            negation_retained=any(c["subject"] == "Ivo" and c["certainty"] == "negated" and _has_item(c["value"], "sword") for c in inventory),
            no_positive_or_empty_inventory=all(c["certainty"] == "negated" and c["value"] != [] for c in inventory),
        )
    elif case_id == "ambiguous_reference":
        checks.update(
            location_retained=any(c["fact_type"] == "location" and c["value"] == "Harbor" for c in facts),
            possession_retained=any(c["fact_type"] == "inventory" and _has_item(c["value"], "map") for c in facts),
            unresolved_not_guessed=bool(facts) and all(c["certainty"] == "unresolved" and c["subject"] not in {"Ada", "Ben", "captain_ada", "captain_ben"} for c in facts),
        )
    elif case_id == "contradiction":
        statuses = [c for c in facts if c["fact_type"] == "status" and c["subject"] == "Mira"]
        checks.update(
            alternatives_preserved=all(isinstance(c["value"], str) for c in statuses)
                and {c["value"] for c in statuses if isinstance(c["value"], str)} == {"dead", "alive"},
            alternatives_require_review=bool(statuses) and all(c["certainty"] in {"uncertain", "unresolved"} for c in statuses),
        )
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly authorize live requests to the configured model")
    parser.add_argument("--output", type=Path, default=Path("reports/world_live_evaluation.json"))
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("Live evaluation is opt-in; pass --run")
    _, model, endpoint = _client_settings()
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "model": model,
              "endpoint": endpoint, "database_written": False,
              "scope": "Six small annotated cases; semantic checks are separate from deterministic importer/verifier tests.",
              "cases": []}
    for case in CASES:
        started = time.monotonic()
        row = {"id": case["id"], "source": case["source"], "annotation": case["annotation"]}
        try:
            result = extract_world_facts(case["source"], existing_world=case["context"])
            row["result"] = result.model_dump()
            row["checks"] = evaluate(case["id"], row["result"]["candidates"])
            row["extraction_errors"] = [name for name, passed in row["checks"].items() if not passed]
        except ExtractionResponseError as exc:
            row["provider_or_evidence_error"] = str(exc)
            row["extraction_errors"] = ["No valid evidence-backed structured response"]
        row["passed"] = not row["extraction_errors"]
        row["review_corrections"] = [] if row["passed"] else [case["correction"]]
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["cases"].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"{row['id']}: {'PASS' if row['passed'] else 'FAIL'} ({row['elapsed_seconds']}s)", flush=True)
    report["passed_cases"] = sum(row["passed"] for row in report["cases"])
    report["total_cases"] = len(CASES)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Report: {args.output.resolve()}", flush=True)
    return 0 if report["passed_cases"] == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
