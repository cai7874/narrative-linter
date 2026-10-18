"""Migration and conservative unknown-state regression tests."""

from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.database import (
    database, get_entity, initialize_database, list_entities, list_rules, upsert_entity,
)
from core.extractor import Claim
from core.verifier import verify_claims


class WorldDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "world.db"

    def tearDown(self):
        self.directory.cleanup()

    def claim(self, **values):
        return Claim(subject="Scout", action="use", items_used=["rope"], text_span="Scout used rope.", line_number=1, **values)

    def test_legacy_migration_preserves_ids_and_never_reseeds_deleted_demo(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("""
                CREATE TABLE entities(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,location TEXT NOT NULL,inventory TEXT NOT NULL,attributes TEXT);
                CREATE TABLE world_rules(rule_id TEXT PRIMARY KEY,category TEXT,forbidden_action TEXT,description TEXT);
                INSERT INTO entities VALUES ('custom-id','Scout','alive','Forest','["rope"]','{}');
                INSERT INTO world_rules VALUES ('custom-rule','magic','fly','No flying');
            """)
        initialize_database(self.path)
        self.assertEqual([entity["id"] for entity in list_entities(self.path)], ["custom-id"])
        self.assertEqual(list_rules(self.path)[0]["enforcement"], "keyword")
        upsert_entity("unknown", "Unknown", None, None, None, {}, self.path)
        self.assertIsNone(get_entity("Unknown", self.path)["inventory"])
        with database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 3)
            connection.execute("DELETE FROM entities")
        initialize_database(self.path)
        self.assertEqual(list_entities(self.path), [])

    def test_v2_provenance_migration_preserves_history_and_expands_candidate_scope(self):
        initialize_database(self.path, seed=False)
        with database(self.path) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE version=3")
            connection.execute("DROP TABLE accepted_changes")
            connection.execute("""CREATE TABLE accepted_changes (
                id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES world_imports(id),
                candidate_id TEXT NOT NULL, fact_type TEXT NOT NULL,
                target_table TEXT NOT NULL, target_id TEXT NOT NULL, field TEXT NOT NULL,
                source_line INTEGER NOT NULL CHECK(source_line>=1), source_excerpt TEXT NOT NULL,
                before_value TEXT, after_value TEXT, UNIQUE(import_id,candidate_id))""")
            connection.execute("INSERT INTO world_imports(id,source_text) VALUES ('i1','Aria gave Bram a key.')")
            connection.execute("""INSERT INTO accepted_changes VALUES
                ('a1','i1','c1','inventory','entities','aria','record',1,'Aria gave Bram a key.','null','{"inventory": []}')""")
        initialize_database(self.path, seed=False)
        with database(self.path) as connection:
            old = dict(connection.execute("SELECT * FROM accepted_changes WHERE id='a1'").fetchone())
            self.assertEqual(old['target_id'], 'aria')
            self.assertEqual(old['after_value'], '{"inventory": []}')
            connection.execute("""INSERT INTO accepted_changes VALUES
                ('a2','i1','c1','inventory','entities','bram','record',1,'Aria gave Bram a key.','null','{"inventory": ["key"]}')""")
            self.assertEqual(connection.execute("SELECT count(*) FROM accepted_changes").fetchone()[0], 2)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO accepted_changes SELECT 'duplicate',import_id,candidate_id,
                    fact_type,target_table,target_id,field,source_line,source_excerpt,before_value,after_value
                    FROM accepted_changes WHERE id='a1'""")
        initialize_database(self.path, seed=False)
        with database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM accepted_changes").fetchone()[0], 2)

    def test_fresh_demo_deletion_survives_restart(self):
        initialize_database(self.path)
        with database(self.path) as connection:
            connection.execute("DELETE FROM entities WHERE id='hero'")
            connection.execute("DELETE FROM world_rules WHERE rule_id='rule_no_teleport'")
        initialize_database(self.path)
        self.assertIsNone(get_entity("Hero", self.path))
        self.assertEqual(len(list_rules(self.path)), 1)

    def test_unknown_inventory_and_known_empty_are_distinct(self):
        initialize_database(self.path, seed=False)
        upsert_entity("scout", "Scout", "alive", None, None, {}, self.path)
        self.assertIsNone(get_entity("Scout", self.path)["inventory"])
        issues = verify_claims([self.claim()], self.path)
        self.assertEqual([issue.issue_type for issue in issues], ["INSUFFICIENT_INFORMATION"])
        upsert_entity("scout", "Scout", "alive", None, [], {}, self.path)
        issues = verify_claims([self.claim()], self.path)
        self.assertEqual([issue.issue_type for issue in issues], ["INVENTORY_DEFICIT"])

    def test_partial_inventory_checks_known_possessions_only(self):
        initialize_database(self.path, seed=False)
        upsert_entity("scout", "Scout", "alive", "Forest", ["rope"], {"_inventory_complete": False}, self.path)
        self.assertEqual(verify_claims([self.claim()], self.path), [])
        claim = Claim(subject="Scout", action="use", items_used=["sword"], text_span="Scout used a sword.", line_number=1)
        self.assertEqual(verify_claims([claim], self.path)[0].issue_type, "INSUFFICIENT_INFORMATION")

    def test_unknown_location_does_not_become_spatial_mismatch(self):
        initialize_database(self.path, seed=False)
        upsert_entity("scout", "Scout", "alive", None, [], {}, self.path)
        upsert_entity("guard", "Guard", "alive", "Forest", [], {}, self.path)
        claim = Claim(subject="Scout", action="interact", target="Guard", text_span="Scout touched Guard.", line_number=1)
        self.assertEqual([issue.issue_type for issue in verify_claims([claim], self.path)], ["INSUFFICIENT_INFORMATION"])

    def test_descriptive_rules_are_stored_but_not_keyword_enforced(self):
        initialize_database(self.path, seed=False)
        upsert_entity("scout", "Scout", "alive", "Forest", ["rope"], {}, self.path)
        with database(self.path) as connection:
            connection.execute("INSERT INTO world_rules VALUES ('prose','setting','use','Only nobles may use rope','descriptive')")
        self.assertEqual(verify_claims([self.claim()], self.path), [])

    def test_provenance_failure_rolls_back_fact_update_and_import(self):
        initialize_database(self.path, seed=False)
        upsert_entity("scout", "Scout", None, None, None, {}, self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            with database(self.path) as connection:
                connection.execute("UPDATE entities SET status='alive' WHERE id='scout'")
                connection.execute("INSERT INTO world_imports(id,source_text) VALUES ('i1','Scout is alive.')")
                connection.execute("INSERT INTO accepted_changes(id,import_id,candidate_id,fact_type,target_table,target_id,field,source_line,source_excerpt) VALUES ('c1','i1','fact1','status','entities','scout','status',0,'Scout is alive.')")
        self.assertIsNone(get_entity("Scout", self.path)["status"])
        with database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM world_imports").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
