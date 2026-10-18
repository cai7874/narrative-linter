"""Human-readable, escaped presentation of world import previews."""

from html import escape
import json


EMPTY_PREVIEW = '<div class="preview-empty">Select facts, then choose <strong>Preview selected changes</strong> to review what will be saved.</div>'


def _text(value):
    return escape(str(value), quote=True)


def _value(value):
    if value is None:
        return "Not recorded"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "None"
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_value(item)}" for key, item in value.items()) or "None"
    return str(value)


def _fields(row):
    if not row:
        return {}
    result = {}
    for key, value in row.items():
        if key in {"id", "rule_id", "subject_id", "object_id", "enforcement", "forbidden_action"}:
            continue
        if key in {"attributes", "inventory"} and isinstance(value, str):
            value = json.loads(value)
        if key == "attributes":
            result.update({name: item for name, item in (value or {}).items() if not name.startswith("_")})
        else:
            result[key] = value
    return result


def render_world_preview(payload):
    """Show semantic changes and evidence without exposing storage identifiers."""
    if not payload:
        return EMPTY_PREVIEW
    changes = payload["changes"]
    active = [change for change in changes if change["effect"] != "skipped"]
    pending = sum(change["effect"] in {"addition", "update"} for change in active)
    blocked = sum(bool(change["errors"]) for change in active)
    unchanged = sum(change["effect"] == "unchanged" for change in active)
    summary = f"{pending} selected {'change' if pending == 1 else 'changes'}"
    if blocked:
        summary += f" · {blocked} {'needs' if blocked == 1 else 'need'} attention"
    if unchanged:
        summary += f" · {unchanged} already match saved facts"
    parts = [f'<div class="world-preview"><h3>Review before saving</h3><p>{_text(summary)}</p>']
    labels = {"entity": "Character", "inventory": "Inventory", "location": "Location",
              "status": "Status", "attribute": "Attribute", "relationship": "Relationship", "rule": "World rule"}
    for change in active:
        kind = change["fact_type"]
        title = f'{change.get("subject", "")} · {labels.get(kind, kind)}'
        if kind == "relationship" or change.get("operation") == "transfer":
            title += f' → {change.get("object") or "Unknown recipient"}'
        state = {"blocked": "Needs correction", "unchanged": "No change", "addition": "New", "update": "Update"}[change["effect"]]
        parts.append(f'<article class="preview-change"><div class="preview-heading"><h4>{_text(title)}</h4><span class="preview-state state-{change["effect"]}">{state}</span></div>')
        if kind == "relationship":
            action = "Remove" if change.get("operation") == "remove" else "Establish"
            parts.append(f'<p>{action} relationship: <strong>{_text(_value(change.get("value")))}</strong></p>')
        for before, after in zip(change["before"] or [], change["after"] or []):
            old, new = _fields(before), _fields(after)
            name = (after or before).get("name")
            if name and (len(change["after"]) > 1 or kind == "inventory"):
                parts.append(f'<p class="preview-owner">{_text(name)}</p>')
            for field in dict.fromkeys([*old, *new]):
                if old.get(field) == new.get(field) or (kind == "relationship" and field == "relationship_type"):
                    continue
                label = "Rule" if field == "description" and kind == "rule" else field.replace("_", " ").capitalize()
                parts.append(f'<div class="preview-field"><strong>{_text(label)}</strong><div class="preview-values"><div><span>Before</span>{_text(_value(old.get(field)))}</div><div><span>After</span>{_text(_value(new.get(field)))}</div></div></div>')
        if change["effect"] == "unchanged":
            parts.append('<p>This fact does not change the saved world.</p>')
        if change["errors"]:
            parts.append(f'<p>Proposed value: <strong>{_text(_value(change.get("value")))}</strong></p>')
        for error in change["errors"]:
            parts.append(f'<p class="preview-error">{_text(error)}. Adjust the selected facts or confirmed names, then preview again.</p>')
        for warning in change["warnings"]:
            if warning != "Selected update replaces the displayed previous values":
                parts.append(f'<p class="preview-note">{_text(warning)}</p>')
        parts.append(f'<blockquote><span>Source · line {change["line_number"]}</span>{_text(change["text_span"])}</blockquote></article>')
    skipped = len(changes) - len(active)
    if skipped:
        parts.append(f'<p class="preview-note">{skipped} unselected facts will not be saved.</p>')
    parts.append('</div>')
    return "".join(parts)
