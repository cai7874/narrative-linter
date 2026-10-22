"""Reviewable world proposals and atomic, provenance-preserving confirmation."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .database import DEFAULT_DATABASE_PATH, database
from .world_extractor import WorldExtractionResult, validate_world_source_spans


class ImportConflictError(ValueError):
    """A preview is invalid, stale, or already committed."""


class ProposedChange(BaseModel):
    candidate_id: str
    effect: str
    before: Any = None
    after: Any = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    writes: list[dict[str, Any]] = Field(default_factory=list)
    text_span: str
    line_number: int
    fact_type: str


class ImportPreview(BaseModel):
    import_id: str
    source_text: str
    baseline: dict[str, list[dict[str, Any]]]
    changes: list[ProposedChange]
    database_path: str

    @property
    def errors(self) -> list[str]:
        return [error for change in self.changes for error in change.errors]


TABLES = ('entities', 'locations', 'relationships', 'world_rules')


def _snapshot(connection):
    return {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1')]
            for table in TABLES}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def prepare_import(source_text: str, extraction: WorldExtractionResult,
                   selected_ids: list[str] | None = None,
                   confirmed_names: list[str] | None = None,
                   database_path=DEFAULT_DATABASE_PATH) -> ImportPreview:
    """Resolve selected proposals against an initialized database without writing facts."""
    validate_world_source_spans(extraction, source_text)
    with database(database_path) as connection:
        baseline = _snapshot(connection)
    working = copy.deepcopy(baseline)
    selected = set(selected_ids or [])
    known_ids = {c.id for c in extraction.candidates}
    if selected - known_ids:
        raise ValueError('Selected candidate IDs do not exist')
    names = set(confirmed_names or [])
    changes = []
    touched = {}
    for candidate in extraction.candidates:
        change = ProposedChange(candidate_id=candidate.id, effect='skipped',
                                text_span=candidate.text_span, line_number=candidate.line_number,
                                fact_type=candidate.fact_type)
        changes.append(change)
        if candidate.id not in selected:
            continue
        before_working = copy.deepcopy(working)
        before_touched = copy.deepcopy(touched)
        try:
            if candidate.certainty not in {'explicit', 'transition'}:
                raise ValueError('Resolve uncertainty, negation, or references before accepting this fact')

            def entity(reference):
                if not reference or not reference.strip():
                    raise ValueError('An entity name or ID is required')
                matches = [e for e in working['entities'] if e['id'] == reference]
                if not matches:
                    matches = [e for e in working['entities'] if e['name'].casefold() == reference.casefold()]
                if len(matches) > 1:
                    raise ValueError(f'Ambiguous entity {reference!r}; use its exact ID')
                if matches:
                    return matches[0]
                if reference not in names:
                    raise ValueError(f'Confirm the new entity name {reference!r} before saving')
                result = dict(id=uuid.uuid4().hex, name=reference, status=None,
                              location=None, inventory=None, attributes='{}')
                working['entities'].append(result)
                return result

            def location(name):
                if not isinstance(name, str) or not name.strip():
                    raise ValueError('Location must be a nonempty name')
                matches = [r for r in working['locations'] if r['name'].casefold() == name.casefold()]
                if len(matches) > 1:
                    raise ValueError('Ambiguous location')
                if matches:
                    return matches[0]
                row = dict(id=uuid.uuid4().hex, name=name, description=None, attributes='{}')
                working['locations'].append(row)
                return row

            c = candidate
            if c.value is None and c.fact_type in {'status', 'location', 'inventory', 'attribute'}:
                change.effect = 'unchanged'
                change.warnings.append('Unknown or omitted value leaves saved facts untouched')
                continue
            if c.fact_type == 'rule':
                if c.operation not in {'set', 'add'}:
                    raise ValueError('Rules support set or add only')
                if not isinstance(c.value, str) or not c.value.strip():
                    raise ValueError('A rule requires its description as a string')
                # Imported prose cannot enable executable keyword checks.
                row = next((r for r in working['world_rules'] if r['description'] == c.value), None)
                if row is None:
                    working['world_rules'].append(dict(rule_id=uuid.uuid4().hex,
                        category=c.field or 'world', forbidden_action=None,
                        description=c.value, enforcement='descriptive'))
                change.warnings.append('Stored for reference; not automatically checked')
            elif c.fact_type == 'location' and c.field == 'definition':
                if c.operation not in {'set', 'add'}:
                    raise ValueError('Location definitions support set or add only')
                location(c.subject)
            else:
                subject = entity(c.subject)
                if c.fact_type == 'entity':
                    if c.operation != 'set' or c.field not in {None, 'name'} or (c.value is not None and c.value != c.subject):
                        raise ValueError('Entity definition only creates a name; use dedicated status, location, inventory or attribute fact types for other fields')
                elif c.fact_type == 'relationship':
                    target = entity(c.object_ref)
                    relation = c.value
                    if not isinstance(relation, str) or not relation.strip():
                        raise ValueError('Relationship type must be a nonempty string')
                    row = next((r for r in working['relationships'] if
                        (r['subject_id'], r['object_id'], r['relationship_type']) ==
                        (subject['id'], target['id'], relation)), None)
                    if c.operation == 'remove':
                        if row:
                            working['relationships'].remove(row)
                    elif c.operation in {'set', 'add'}:
                        if row is None:
                            working['relationships'].append(dict(id=uuid.uuid4().hex,
                                subject_id=subject['id'], object_id=target['id'],
                                relationship_type=relation, description=None))
                    else:
                        raise ValueError('Unsupported relationship operation')
                    change.warnings.append('Relationships are stored; not automatically checked')
                elif c.fact_type in {'status', 'location', 'attribute'}:
                    if c.operation != 'set':
                        raise ValueError('This field requires the set operation')
                    field = c.field if c.fact_type == 'attribute' else c.fact_type
                    if not field:
                        raise ValueError('Attribute requires a field name')
                    key = (subject['id'], c.fact_type, field)
                    if key in touched and touched[key] != c.value and c.certainty != 'transition':
                        raise ValueError('Conflicting selected facts; deselect one or explicitly resolve as a transition')
                    touched[key] = c.value
                    if c.fact_type == 'status':
                        if c.value not in {None, 'alive', 'dead', 'unconscious', 'injured', 'missing'}:
                            raise ValueError('Unsupported status')
                        subject['status'] = c.value
                    elif c.fact_type == 'location':
                        subject['location'] = location(c.value)['name'] if c.value is not None else None
                    else:
                        attrs = json.loads(subject['attributes'] or '{}')
                        if field.startswith('_'):
                            raise ValueError('Reserved attribute name')
                        attrs[field] = c.value
                        subject['attributes'] = _encode(attrs)
                elif c.fact_type == 'inventory':
                    values = [c.value] if isinstance(c.value, str) else c.value
                    if values is not None and (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values)):
                        raise ValueError('Inventory must be an item string, list, or null')
                    items = list(dict.fromkeys(v.strip().lower().replace(' ', '_') for v in values or []))
                    for item in items:
                        key = (subject['id'], 'ownership', item)
                        direction = 'absent' if c.operation in {'remove', 'transfer'} else 'present'
                        if key in touched and touched[key] != direction and c.certainty != 'transition':
                            raise ValueError('Conflicting selected ownership facts; resolve as an explicit transition or deselect one')
                        touched[key] = direction
                    inventory = json.loads(subject['inventory']) if subject['inventory'] is not None else None
                    attrs = json.loads(subject['attributes'] or '{}')
                    if c.operation == 'set':
                        key = (subject['id'], 'inventory', 'set')
                        if key in touched and touched[key] != items and c.certainty != 'transition':
                            raise ValueError('Conflicting inventory replacements')
                        touched[key] = items
                        subject['inventory'] = _encode(items) if values is not None else None
                        attrs['_inventory_complete'] = values is not None
                    elif c.operation == 'add':
                        subject['inventory'] = _encode(list(dict.fromkeys((inventory or []) + items)))
                        if inventory is None:
                            attrs['_inventory_complete'] = False
                    elif c.operation in {'remove', 'transfer'}:
                        if not items:
                            raise ValueError('Removal or transfer requires at least one item')
                        if inventory is None or any(item not in inventory for item in items):
                            raise ValueError('Cannot remove or transfer an item without established ownership')
                        subject['inventory'] = _encode([v for v in inventory if v not in items])
                        if c.operation == 'transfer':
                            target = entity(c.object_ref)
                            if target['id'] == subject['id']:
                                raise ValueError('Transfer recipient must differ from giver')
                            owned = json.loads(target['inventory']) if target['inventory'] is not None else None
                            target['inventory'] = _encode(list(dict.fromkeys((owned or []) + items)))
                            if owned is None:
                                target_attrs = json.loads(target['attributes'] or '{}')
                                target_attrs['_inventory_complete'] = False
                                target['attributes'] = _encode(target_attrs)
                    else:
                        raise ValueError('Unsupported inventory operation')
                    subject['attributes'] = _encode(attrs)
                else:
                    raise ValueError('Unsupported fact type')
            for table in TABLES:
                pk = 'rule_id' if table == 'world_rules' else 'id'
                old = {r[pk]: r for r in before_working[table]}
                new = {r[pk]: r for r in working[table]}
                for rid in old.keys() | new.keys():
                    if old.get(rid) != new.get(rid):
                        change.writes.append(dict(table=table, id=rid, before=copy.deepcopy(old.get(rid)), after=copy.deepcopy(new.get(rid))))
            change.before = [w['before'] for w in change.writes]
            change.after = [w['after'] for w in change.writes]
            change.effect = ('unchanged' if not change.writes else
                             'update' if any(w['before'] for w in change.writes) else 'addition')
            if any(w['before'] for w in change.writes):
                change.warnings.append('Selected update replaces the displayed previous values')
        except (ValueError, TypeError) as exc:
            working = before_working
            touched = before_touched
            change.effect = 'blocked'
            change.errors.append(str(exc))
    return ImportPreview(import_id=uuid.uuid4().hex, source_text=source_text,
                         baseline=baseline, changes=changes,
                         database_path=str(Path(database_path).resolve()))


def confirm_import(preview: ImportPreview, database_path=DEFAULT_DATABASE_PATH) -> str:
    """Commit a frozen server-side preview once, checking for concurrent edits."""
    if preview.database_path != str(Path(database_path).resolve()):
        raise ImportConflictError('Preview belongs to a different database')
    if preview.errors:
        raise ImportConflictError('; '.join(preview.errors))
    if not any(c.writes for c in preview.changes):
        raise ImportConflictError('No changes selected')
    with database(database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute('SELECT 1 FROM world_imports WHERE id=?', (preview.import_id,)).fetchone():
            raise ImportConflictError('This import has already been saved')
        if _snapshot(connection) != preview.baseline:
            raise ImportConflictError('Saved facts changed since preview. Generate and review a fresh preview')
        connection.execute('INSERT INTO world_imports(id,source_text) VALUES (?,?)',
                           (preview.import_id, preview.source_text))
        for change in preview.changes:
            for write in change.writes:
                table = write['table']
                pk = 'rule_id' if table == 'world_rules' else 'id'
                row = write['after']
                if row is None:
                    connection.execute(f'DELETE FROM {table} WHERE {pk}=?', (write['id'],))
                else:
                    columns = list(row)
                    assignments = ','.join(f'{col}=excluded.{col}' for col in columns if col != pk)
                    connection.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT({pk}) DO UPDATE SET {assignments}", list(row.values()))
                connection.execute('''INSERT INTO accepted_changes
                    (id,import_id,candidate_id,fact_type,target_table,target_id,field,
                     source_line,source_excerpt,before_value,after_value)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (uuid.uuid4().hex, preview.import_id, change.candidate_id, change.fact_type,
                     table, write['id'], 'record', change.line_number, change.text_span,
                     _encode(write['before']), _encode(row)))
    return preview.import_id
