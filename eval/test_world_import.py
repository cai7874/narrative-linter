"""Offline acceptance tests for reviewed world imports and transaction boundaries."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.database import (database, get_entity, initialize_database, list_entities,
                           list_imports, list_accepted_changes, list_relationships,
                           list_rules, upsert_entity)
from core.extractor import Claim
from core.verifier import verify_claims
from core.world_extractor import WorldExtractionResult, WorldFactCandidate
from core.world_import import ImportConflictError, confirm_import, prepare_import


class WorldImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'world.db'
        initialize_database(self.path, seed=False)
        self.source = 'Aria is alive. Aria carries a key. Aria gives the key to Bram.'

    def tearDown(self):
        self.directory.cleanup()

    def fact(self, identifier='c1', **changes):
        values = dict(id=identifier, fact_type='status', subject='Aria', object_ref=None,
                      field=None, value='alive', operation='set', certainty='explicit',
                      text_span='Aria is alive.', line_number=1)
        values.update(changes)
        return WorldFactCandidate(**values)

    def preview(self, *facts, selected=None, names=None):
        return prepare_import(self.source, WorldExtractionResult(candidates=list(facts)),
                              selected_ids=selected if selected is not None else [fact.id for fact in facts],
                              confirmed_names=names or [], database_path=self.path)

    def save(self, preview):
        return confirm_import(preview, self.path)

    def entity(self, identifier='aria', name='Aria', status='alive', location='Tower', inventory=None):
        upsert_entity(identifier, name, status, location, inventory, {}, self.path)

    def test_conflicting_ownership_requires_transition_resolution(self):
        self.entity(inventory=['key'])
        first = self.fact('c1', fact_type='inventory', value='key', operation='add')
        second = self.fact('c2', fact_type='inventory', value='key', operation='remove')
        blocked = self.preview(first, second)
        self.assertTrue(blocked.errors)
        resolved = self.preview(first, second.model_copy(update={'certainty': 'transition'}))
        self.assertEqual(resolved.errors, [])
        self.save(resolved)
        self.assertEqual(get_entity('Aria', self.path)['inventory'], [])

    def test_provenance_preserves_each_intermediate_record(self):
        first = self.fact()
        second = self.fact('c2', fact_type='inventory', value='key', operation='add')
        preview = self.preview(first, second, names=['Aria'])
        self.assertIsNone(preview.changes[0].after[0]['inventory'])
        self.assertIsNone(preview.changes[1].before[0]['inventory'])
        self.assertEqual(json.loads(preview.changes[1].after[0]['inventory']), ['key'])
        self.save(preview)
        records = list_accepted_changes(self.path)
        self.assertIsNone(records[0]['after_value']['inventory'])
        self.assertEqual(json.loads(records[1]['after_value']['inventory']), ['key'])

    def test_selected_only_and_cancel_do_not_mutate_saved_world(self):
        first = self.fact()
        second = self.fact('c2', fact_type='inventory', value='key', operation='add')
        preview = self.preview(first, second, selected=['c1'], names=['Aria'])
        self.assertEqual(list_entities(self.path), [])
        self.assertEqual(list_imports(self.path), [])
        self.assertEqual(preview.changes[1].effect, 'skipped')
        self.save(preview)
        self.assertIsNone(get_entity('Aria', self.path)['inventory'])
        self.assertEqual(len(list_accepted_changes(self.path)), 1)

    def test_preview_reads_committed_world_while_another_connection_is_writing(self):
        self.entity()
        with database(self.path) as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE entities SET status='missing' WHERE id='aria'")
            preview = self.preview(self.fact(value='injured'))
            self.assertEqual(preview.errors, [])
            self.assertEqual(preview.changes[0].before[0]['status'], 'alive')
            self.assertEqual(preview.changes[0].after[0]['status'], 'injured')
            self.assertEqual(get_entity('Aria', self.path)['status'], 'alive')
            self.assertEqual(list_imports(self.path), [])

    def test_new_names_require_explicit_confirmation_and_missing_fields_stay_unknown(self):
        blocked = self.preview(self.fact())
        self.assertTrue(blocked.errors)
        with self.assertRaises(ImportConflictError):
            self.save(blocked)
        preview = self.preview(self.fact(), names=['Aria'])
        self.save(preview)
        entity = get_entity('Aria', self.path)
        self.assertEqual(entity['status'], 'alive')
        self.assertIsNone(entity['location'])
        self.assertIsNone(entity['inventory'])

    def test_identical_fact_is_unchanged_and_omitted_fields_are_preserved(self):
        self.entity(inventory=['key'])
        preview = self.preview(self.fact())
        self.assertEqual(preview.changes[0].effect, 'unchanged')
        with self.assertRaises(ImportConflictError):
            self.save(preview)
        preview = self.preview(self.fact(value='injured'))
        self.assertEqual(preview.changes[0].before[0]['status'], 'alive')
        self.save(preview)
        saved = get_entity('Aria', self.path)
        self.assertEqual(saved['inventory'], ['key'])
        self.assertEqual(saved['location'], 'Tower')

    def test_conflicting_selected_assertions_block_entire_confirmation(self):
        self.entity()
        preview = self.preview(self.fact(), self.fact('c2', value='dead'))
        self.assertTrue(preview.errors)
        with self.assertRaises(ImportConflictError):
            self.save(preview)
        self.assertEqual(get_entity('Aria', self.path)['status'], 'alive')
        self.assertEqual(list_imports(self.path), [])

    def test_transfer_changes_both_owners_with_two_provenance_records(self):
        self.entity(inventory=['key', 'rope'])
        self.entity('bram', 'Bram', inventory=[])
        preview = self.preview(self.fact(fact_type='inventory', operation='transfer',
                                        value='key', object_ref='Bram', certainty='transition'))
        self.assertFalse(preview.errors)
        self.assertEqual(len(preview.changes[0].writes), 2)
        self.save(preview)
        self.assertEqual(get_entity('Aria', self.path)['inventory'], ['rope'])
        self.assertEqual(get_entity('Bram', self.path)['inventory'], ['key'])
        provenance = list_accepted_changes(self.path)
        self.assertEqual(len(provenance), 2)
        self.assertEqual({row['target_id'] for row in provenance}, {'aria', 'bram'})
        self.assertTrue(all(row['candidate_id'] == 'c1' and row['source_excerpt'] == 'Aria is alive.' for row in provenance))
        self.assertEqual(list_imports(self.path)[0]['source_text'], self.source)

    def test_stale_preview_and_duplicate_confirmation_are_rejected(self):
        self.entity()
        preview = self.preview(self.fact(value='injured'))
        self.entity(status='missing')
        with self.assertRaisesRegex(ImportConflictError, 'changed since preview'):
            self.save(preview)
        self.assertEqual(get_entity('Aria', self.path)['status'], 'missing')
        preview = self.preview(self.fact(value='injured'))
        self.save(preview)
        with self.assertRaisesRegex(ImportConflictError, 'already been saved'):
            self.save(preview)
        self.assertEqual(len(list_imports(self.path)), 1)
        self.assertEqual(len(list_accepted_changes(self.path)), 1)

    def test_database_failure_rolls_back_world_import_and_provenance(self):
        self.entity(inventory=['key'])
        self.entity('bram', 'Bram', inventory=[])
        preview = self.preview(self.fact(fact_type='inventory', operation='transfer', value='key',
                                        object_ref='Bram', certainty='transition'))
        with database(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_second_evidence BEFORE INSERT ON accepted_changes WHEN (SELECT count(*) FROM accepted_changes)=1 BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'forced failure'):
            self.save(preview)
        self.assertEqual(get_entity('Aria', self.path)['inventory'], ['key'])
        self.assertEqual(get_entity('Bram', self.path)['inventory'], [])
        self.assertEqual(list_imports(self.path), [])
        self.assertEqual(list_accepted_changes(self.path), [])

    def test_relationships_and_rules_are_explicitly_not_automatically_checked(self):
        self.entity()
        self.entity('bram', 'Bram')
        preview = self.preview(
            self.fact(fact_type='relationship', object_ref='Bram', value='sibling'),
            self.fact('c2', fact_type='rule', subject='Magic law', field='magic', value='Only nobles can cast.'),
        )
        self.assertFalse(preview.errors)
        self.assertTrue(all('not automatically checked' in ' '.join(change.warnings) for change in preview.changes))
        self.save(preview)
        self.assertEqual(list_relationships(self.path)[0]['relationship_type'], 'sibling')
        self.assertEqual(list_rules(self.path)[0]['enforcement'], 'descriptive')
        self.assertIsNone(list_rules(self.path)[0]['forbidden_action'])

    def test_saved_facts_feed_next_audit_while_replay_is_not_persisted(self):
        self.entity(inventory=['key'])
        preview = self.preview(self.fact(fact_type='location', value='Forest'))
        self.save(preview)
        claims = [Claim(subject='Aria', action='move', claimed_location='Tower', text_span='Aria went to Tower.', line_number=1),
                  Claim(subject='Aria', action='use', items_used=['key'], claimed_location='Tower', text_span='Aria used key.', line_number=2)]
        self.assertEqual(verify_claims(claims, self.path), [])
        self.assertEqual(get_entity('Aria', self.path)['location'], 'Forest')
        self.assertEqual(len(list_imports(self.path)), 1)

    def test_ambiguous_case_insensitive_name_requires_resolution(self):
        self.entity()
        self.entity('aria2', 'ARIA')
        preview = self.preview(self.fact(subject='ArIa', value='injured'))
        self.assertTrue(preview.errors)
        self.assertIn('Ambiguous', preview.errors[0])
        self.assertEqual(get_entity('Aria', self.path)['status'], 'alive')
        resolved = self.preview(self.fact(subject='aria', value='injured'))
        self.assertFalse(resolved.errors)
        self.save(resolved)
        with database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT status FROM entities WHERE id='aria'").fetchone()[0], 'injured')
            self.assertEqual(connection.execute("SELECT status FROM entities WHERE id='aria2'").fetchone()[0], 'alive')

    def test_null_candidates_leave_all_existing_fields_untouched(self):
        self.entity(inventory=['key'])
        upsert_entity('aria', 'Aria', 'alive', 'Tower', ['key'], {'faction': 'rebels'}, self.path)
        facts = [self.fact(f'null-{kind}', fact_type=kind, value=None,
                           field='faction' if kind == 'attribute' else None)
                 for kind in ('status', 'location', 'inventory', 'attribute')]
        preview = self.preview(*facts, self.fact('change', value='injured'))
        self.assertTrue(all(change.effect == 'unchanged' and not change.writes
                            for change in preview.changes[:-1]))
        self.save(preview)
        saved = get_entity('Aria', self.path)
        self.assertEqual(saved['status'], 'injured')
        self.assertEqual(saved['location'], 'Tower')
        self.assertEqual(saved['inventory'], ['key'])
        self.assertEqual(saved['attributes'], {'faction': 'rebels'})
        self.assertEqual(len(list_accepted_changes(self.path)), 1)

    def test_rule_removal_and_invalid_entity_fields_are_blocked(self):
        self.entity()
        invalid = [self.fact(fact_type='rule', operation='remove', value='Magic is forbidden.'),
                   self.fact(fact_type='entity', field='status', value='dead'),
                   self.fact(fact_type='entity', field='name', value='Someone Else')]
        for candidate in invalid:
            with self.subTest(fact_type=candidate.fact_type, field=candidate.field):
                preview = self.preview(candidate)
                self.assertTrue(preview.errors)
                self.assertEqual(preview.changes[0].writes, [])
                with self.assertRaises(ImportConflictError):
                    self.save(preview)
        self.assertEqual(get_entity('Aria', self.path)['status'], 'alive')
        self.assertEqual(list_rules(self.path), [])

    def test_failed_candidate_does_not_poison_later_conflict_tracking(self):
        self.entity()
        preview = self.preview(self.fact(value='flying'), self.fact('valid', value='injured'))
        self.assertEqual(preview.changes[0].effect, 'blocked')
        self.assertFalse(preview.changes[1].errors)
        self.assertEqual(preview.changes[1].effect, 'update')
        self.assertEqual(preview.changes[1].after[0]['status'], 'injured')
        self.assertEqual(get_entity('Aria', self.path)['status'], 'alive')

    def test_negated_and_uncertain_facts_cannot_be_saved_without_review(self):
        for certainty in ('negated', 'uncertain', 'unresolved'):
            preview = self.preview(self.fact(certainty=certainty), names=['Aria'])
            self.assertTrue(preview.errors)
            with self.assertRaises(ImportConflictError):
                self.save(preview)
        self.assertEqual(list_entities(self.path), [])


if __name__ == '__main__':
    unittest.main()
