"""Synthetic acceptance checks; --live optionally evaluates real extraction too."""
import argparse
import json
import tempfile
from pathlib import Path
from core.audit import audit_claims
from core.database import initialize_database
from core.extractor import ExtractionResult, extract_claims, validate_source_spans

ROOT = Path(__file__).resolve().parent


def evaluate(cases, live=False):
    rows = []
    for case in cases:
        row = {"case_id": case["case_id"], "expected": case["expected"], "expected_verdict": case["expected_verdict"]}
        try:
            extraction = extract_claims(case["text"]) if live else ExtractionResult(claims=case["claims"])
            validate_source_spans(extraction, case["text"])
            with tempfile.TemporaryDirectory() as directory:
                db = Path(directory) / "world.db"
                initialize_database(db)
                audit = audit_claims(extraction.claims, db)
            actual = {(i["line_number"], i["issue_type"]) for i in audit["issues"]}
            expected = {(i["line_number"], i["issue_type"]) for i in case["expected"]}
            row.update(claims=extraction.model_dump()["claims"], issues=audit["issues"], verdict=audit["verdict"],
                       tp=len(actual & expected), fp=len(actual - expected), fn=len(expected - actual),
                       exact_match=actual == expected and audit["verdict"] == case["expected_verdict"])
        except (RuntimeError, ValueError) as error:
            row.update(error=str(error), exact_match=False, tp=0, fp=0, fn=len(case["expected"]))
        rows.append(row)
    tp, fp, fn = (sum(r[key] for r in rows) for key in ("tp", "fp", "fn"))
    return {"mode": "live_extraction" if live else "gold_claim_replay", "case_count": len(rows),
            "exact_matches": sum(r["exact_match"] for r in rows), "tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "note": "Synthetic acceptance cases, not a representative or held-out narrative benchmark. Live mode has no LLM-only baseline.", "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the configured model endpoint; default is offline")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "reports" / ("live_evaluation.json" if args.live else "acceptance.json")
    if output.exists() and args.live:
        parser.error("Choose a new --output path to preserve the existing live run")
    result = evaluate(json.loads((ROOT / "examples/acceptance_cases.json").read_text())["cases"], args.live)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{result['mode']}: {result['exact_matches']}/{result['case_count']} exact acceptance matches; TP={result['tp']}, FP={result['fp']}, FN={result['fn']}")
    print(result["note"])
    print(f"Saved {output.name}")
    if not args.live and result["exact_matches"] != result["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
