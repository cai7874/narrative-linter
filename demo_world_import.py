"""Offline import review, explicit confirmation, and nonpersistent audit example."""
from pathlib import Path
from tempfile import TemporaryDirectory

from core.audit import audit_claims
from core.database import get_entity, initialize_database, list_accepted_changes
from core.extractor import Claim
from core.world_extractor import WorldExtractionResult
from core.world_import import confirm_import, prepare_import

SOURCE = 'Mira is alive in North Tower. Mira owns a brass key.'
FIXED_RESPONSE = {'candidates': [
    {'id': 'status', 'fact_type': 'status', 'subject': 'Mira', 'object_ref': None,
     'field': 'status', 'value': 'alive', 'operation': 'set', 'certainty': 'explicit',
     'text_span': SOURCE, 'line_number': 1},
    {'id': 'place', 'fact_type': 'location', 'subject': 'Mira', 'object_ref': None,
     'field': 'location', 'value': 'North Tower', 'operation': 'set', 'certainty': 'explicit',
     'text_span': SOURCE, 'line_number': 1},
    {'id': 'key', 'fact_type': 'inventory', 'subject': 'Mira', 'object_ref': None,
     'field': 'inventory', 'value': ['brass_key'], 'operation': 'add', 'certainty': 'explicit',
     'text_span': SOURCE, 'line_number': 1},
]}


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'example.db'
        initialize_database(path, seed=False)
        extracted = WorldExtractionResult.model_validate(FIXED_RESPONSE)
        preview = prepare_import(SOURCE, extracted, ['status', 'place', 'key'], ['Mira'], path)
        print('PROPOSED CHANGES (database still empty):')
        for change in preview.changes:
            print(change.model_dump_json(exclude={'writes'}))
        confirm_import(preview, path)  # Explicit confirmation for this disposable example.
        print('SAVED:', get_entity('Mira', path))
        print('PROVENANCE RECORDS:', len(list_accepted_changes(path)))
        event = Claim(subject='Mira', action='used', items_used=['brass_key'],
                      claimed_location='North Tower', text_span='Mira used the brass key.', line_number=1)
        print('AUDIT:', audit_claims([event], path)['verdict'])
        print('AFTER AUDIT:', get_entity('Mira', path))


if __name__ == '__main__':
    main()
