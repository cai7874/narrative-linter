"""Versioned SQLite world storage with explicit unknown values.

Call initialize_database() at application startup or in test setup before access.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "world.db"
ENTITY_STATUSES = {"alive", "dead", "unconscious", "injured", "missing"}
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT CHECK(status IS NULL OR status IN ('alive', 'dead', 'unconscious', 'injured', 'missing')),
    location TEXT,
    inventory TEXT,
    attributes TEXT
);
CREATE TABLE IF NOT EXISTS world_rules (
    rule_id TEXT PRIMARY KEY,
    category TEXT,
    forbidden_action TEXT,
    description TEXT,
    enforcement TEXT NOT NULL DEFAULT 'keyword' CHECK(enforcement IN ('keyword', 'descriptive'))
);
"""

SEED_ENTITIES = (
    ("hero", "Hero", "alive", "Castle Courtyard", ["silver_dagger", "torch"], {"mana": 50, "faction": "rebels"}),
    ("mentor", "Mentor", "dead", "Castle Crypt", ["old_key"], {"faction": "rebels"}),
    ("guard", "Guard", "alive", "Castle Courtyard", ["spear"], {"faction": "royal_guard"}),
)
SEED_RULES = (
    ("rule_no_teleport", "magic_system", "teleport", "Teleportation is forbidden in the current world."),
    ("rule_no_blood_magic", "magic_system", "blood_magic", "Blood magic is forbidden by the royal decree."),
)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def connect(database_path: str | Path = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    """Open a SQLite connection configured for concurrent reads."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def database(database_path: str | Path = DEFAULT_DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    """Yield a transactional database connection."""
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _statements(connection: sqlite3.Connection, sql: str) -> None:
    # Unlike executescript(), individual statements preserve the transaction.
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def initialize_database(database_path: str | Path = DEFAULT_DATABASE_PATH, seed: bool = True) -> None:
    """Migrate without changing existing facts; initialize the demo at most once."""
    with database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        legacy = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities'").fetchone() is not None
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        if 1 not in versions:
            _statements(connection, SCHEMA)
            # Existing installations have already had their demo initialization.
            if legacy:
                connection.execute("INSERT OR IGNORE INTO app_metadata VALUES ('demo_initialized', '1')")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        if 2 not in versions:
            columns = {row[1]: row for row in connection.execute("PRAGMA table_info(entities)")}
            if any(columns[name][3] for name in ("status", "location", "inventory")):
                connection.execute("ALTER TABLE entities RENAME TO entities_legacy")
                _statements(connection, SCHEMA)
                connection.execute("INSERT INTO entities SELECT id,name,status,location,inventory,attributes FROM entities_legacy")
                connection.execute("DROP TABLE entities_legacy")
            if "enforcement" not in {row[1] for row in connection.execute("PRAGMA table_info(world_rules)")}:
                connection.execute("ALTER TABLE world_rules ADD COLUMN enforcement TEXT NOT NULL DEFAULT 'keyword' CHECK(enforcement IN ('keyword', 'descriptive'))")
            _statements(connection, """
                CREATE TABLE IF NOT EXISTS locations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    description TEXT, attributes TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL REFERENCES entities(id),
                    object_id TEXT NOT NULL REFERENCES entities(id),
                    relationship_type TEXT NOT NULL,
                    description TEXT,
                    UNIQUE(subject_id, object_id, relationship_type)
                );
                CREATE TABLE IF NOT EXISTS world_imports (
                    id TEXT PRIMARY KEY, source_text TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS accepted_changes (
                    id TEXT PRIMARY KEY,
                    import_id TEXT NOT NULL REFERENCES world_imports(id),
                    candidate_id TEXT NOT NULL, fact_type TEXT NOT NULL,
                    target_table TEXT NOT NULL, target_id TEXT NOT NULL,
                    field TEXT NOT NULL, source_line INTEGER NOT NULL CHECK(source_line >= 1),
                    source_excerpt TEXT NOT NULL, before_value TEXT, after_value TEXT,
                    UNIQUE(import_id, candidate_id, target_table, target_id)
                );
                CREATE INDEX IF NOT EXISTS accepted_changes_import ON accepted_changes(import_id);
            """)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (2)")
        if 3 not in versions:
            # A single candidate can affect several records (for example transfers).
            # Rebuild the provenance table to expand the old v2 unique constraint.
            connection.execute("""CREATE TABLE accepted_changes_v3 (
                id TEXT PRIMARY KEY,
                import_id TEXT NOT NULL REFERENCES world_imports(id),
                candidate_id TEXT NOT NULL, fact_type TEXT NOT NULL,
                target_table TEXT NOT NULL, target_id TEXT NOT NULL,
                field TEXT NOT NULL, source_line INTEGER NOT NULL CHECK(source_line >= 1),
                source_excerpt TEXT NOT NULL, before_value TEXT, after_value TEXT,
                UNIQUE(import_id, candidate_id, target_table, target_id)
            )""")
            connection.execute("INSERT INTO accepted_changes_v3 SELECT * FROM accepted_changes ORDER BY rowid")
            connection.execute("DROP TABLE accepted_changes")
            connection.execute("ALTER TABLE accepted_changes_v3 RENAME TO accepted_changes")
            connection.execute("CREATE INDEX accepted_changes_import ON accepted_changes(import_id)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
        initialized = connection.execute("SELECT 1 FROM app_metadata WHERE key='demo_initialized'").fetchone()
        if seed and not initialized:
            connection.executemany(
                "INSERT OR IGNORE INTO entities(id,name,status,location,inventory,attributes) VALUES (?,?,?,?,?,?)",
                ((entity_id, name, status, location, _json_text(inventory), _json_text(attributes))
                 for entity_id, name, status, location, inventory, attributes in SEED_ENTITIES),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO world_rules(rule_id,category,forbidden_action,description) VALUES (?,?,?,?)", SEED_RULES,
            )
            connection.execute("INSERT INTO app_metadata VALUES ('demo_initialized', '1')")


def _entity(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["inventory"] = json.loads(item["inventory"]) if item["inventory"] is not None else None
    item["attributes"] = json.loads(item["attributes"] or "{}")
    return item


def list_entities(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    """Return entities, preserving unknown inventory as None and empty as []."""
    with database(database_path) as connection:
        return [_entity(row) for row in connection.execute("SELECT * FROM entities ORDER BY name")]


def get_entity(name: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> dict[str, Any] | None:
    """Find an entity by its case-insensitive display name."""
    with database(database_path) as connection:
        row = connection.execute("SELECT * FROM entities WHERE lower(name) = lower(?)", (name,)).fetchone()
        return _entity(row) if row else None


def list_rules(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    """Return rules and their explicit verifier enforcement mode."""
    with database(database_path) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM world_rules ORDER BY rule_id")]


def list_locations(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    with database(database_path) as connection:
        return [{**dict(row), "attributes": json.loads(row["attributes"] or "{}")} for row in connection.execute("SELECT * FROM locations ORDER BY name")]


def list_relationships(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    with database(database_path) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM relationships ORDER BY id")]


def list_imports(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    with database(database_path) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM world_imports ORDER BY created_at, id")]


def list_accepted_changes(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    with database(database_path) as connection:
        return [{**dict(row), "before_value": json.loads(row["before_value"]) if row["before_value"] is not None else None,
                 "after_value": json.loads(row["after_value"]) if row["after_value"] is not None else None}
                for row in connection.execute("SELECT * FROM accepted_changes ORDER BY rowid")]


def upsert_entity(
    entity_id: str, name: str, status: str | None, location: str | None,
    inventory: list[str] | None, attributes: dict[str, Any],
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Insert or replace a manually edited entity; None means unknown."""
    if status is not None and status not in ENTITY_STATUSES:
        raise ValueError(f"Unsupported entity status: {status}")
    if not entity_id.strip() or not name.strip():
        raise ValueError("Entity ID and name are required")
    if inventory is not None and (not isinstance(inventory, list) or any(not isinstance(item, str) or not item.strip() for item in inventory)):
        raise ValueError("Inventory must be a list of nonempty item names or unknown")
    if not isinstance(attributes, dict):
        raise ValueError("Attributes must be an object")
    with database(database_path) as connection:
        connection.execute(
            """INSERT INTO entities (id,name,status,location,inventory,attributes) VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,status=excluded.status,
            location=excluded.location,inventory=excluded.inventory,attributes=excluded.attributes""",
            (entity_id, name.strip(), status, location.strip() or None if location is not None else None,
             _json_text(inventory) if inventory is not None else None, _json_text(attributes)),
        )
