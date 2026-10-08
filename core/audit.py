"""Coverage-aware presentation shared by the offline demo and dashboard."""
from .database import DEFAULT_DATABASE_PATH
from .verifier import verify_claims


def audit_claims(claims, database_path=DEFAULT_DATABASE_PATH):
    if not claims:
        return {
            "verdict": "NOT CHECKED", "claim_count": 0, "issues": [],
            "report": "No events were extracted. No consistency checks were performed.",
        }
    issues = verify_claims(claims, database_path)
    verdict = ("CONFLICT DETECTED" if any(i.severity == "ERROR" for i in issues)
               else "PARTIAL CHECK" if issues else "CHECKS CLEAR")
    report = "\n\n".join(
        f"[{i.severity}] line {i.line_number} {i.issue_type}\n"
        f"  {i.flagged_text}\n  {i.explanation}\n  Evidence: {i.evidence_source}"
        for i in issues
    ) or "No implemented invariant was violated by the extracted events."
    report += "\n\nScope: checks cover extracted events and the implemented rules only; omitted events are not verified."
    return {"verdict": verdict, "claim_count": len(claims),
            "issues": [i.model_dump() for i in issues], "report": report}
