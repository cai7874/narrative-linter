"""Offline narrative audit demo; no model server or network access needed."""
import argparse
import json
import tempfile
from pathlib import Path
from core.audit import audit_claims
from core.database import initialize_database
from core.extractor import ExtractionResult, validate_source_spans

EXAMPLES = Path(__file__).resolve().parent / "examples" / "acceptance_cases.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="invalid_transfer_does_not_create_item")
    args = parser.parse_args()
    cases = json.loads(EXAMPLES.read_text())["cases"]
    case = next((c for c in cases if c["case_id"] == args.case), None)
    if case is None:
        parser.error("Available cases: " + ", ".join(c["case_id"] for c in cases))
    extraction = ExtractionResult(claims=case["claims"])
    validate_source_spans(extraction, case["text"])
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "world.db"
        initialize_database(db)
        result = audit_claims(extraction.claims, db)
    print(case["text"] + "\n\n" + result["verdict"] + "\n" + result["report"])


if __name__ == "__main__":
    main()
