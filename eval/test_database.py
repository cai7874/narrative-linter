"""Database layer tests."""

import tempfile
import unittest
from pathlib import Path

from core.database import (
    connect, database, get_entity, initialize_database, list_accepted_changes,
    list_entities, list_imports, list_locations, list_relationships, list_rules,
    upsert_entity,
)


class DatabaseTests(unittest.TestCase):
    def test_schema_seed_and_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            initialize_database(path)
            connection = connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            finally:
                connection.close()
            self.assertEqual(len(list_entities(path)), 3)
            self.assertEqual(get_entity("Mentor", path)["status"], "dead")

    def test_upsert_entity_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            initialize_database(path, seed=False)
            upsert_entity("scout", "Scout", "alive", "Forest", ["rope"], {"faction": "rebels"}, path)
            entity = get_entity("Scout", path)
            self.assertEqual(entity["inventory"], ["rope"])
            self.assertEqual(entity["attributes"]["faction"], "rebels")

    def test_reads_use_committed_world_while_another_connection_is_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            initialize_database(path)
            with database(path) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE entities SET status='injured' WHERE id='hero'")
                self.assertEqual(get_entity("Hero", path)["status"], "alive")
                self.assertEqual(len(list_entities(path)), 3)
                self.assertEqual(len(list_rules(path)), 2)
                self.assertEqual(list_locations(path), [])
                self.assertEqual(list_relationships(path), [])
                self.assertEqual(list_imports(path), [])
                self.assertEqual(list_accepted_changes(path), [])


if __name__ == "__main__":
    unittest.main()
