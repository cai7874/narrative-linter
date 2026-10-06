"""Database layer tests."""

import tempfile
import unittest
from pathlib import Path

from core.database import connect, get_entity, initialize_database, list_entities, upsert_entity


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


if __name__ == "__main__":
    unittest.main()
