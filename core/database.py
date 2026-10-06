"""SQLite-backed world state and rule storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "world.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('alive', 'dead', 'unconscious', 'injured', 'missing')),
    location TEXT NOT NULL,
    inventory TEXT NOT NULL,
    attributes TEXT
);

CREATE TABLE IF NOT EXISTS world_rules (
    rule_id TEXT PRIMARY KEY,
    category TEXT,
    forbidden_action TEXT,
    description TEXT
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


def initialize_database(database_path: str | Path = DEFAULT_DATABASE_PATH, seed: bool = True) -> None:
    """Create the schema and optionally insert idempotent seed data."""
    with database(database_path) as connection:
        connection.executescript(SCHEMA)
        if seed:
            connection.executemany(
                """
                INSERT OR IGNORE INTO entities
                    (id, name, status, location, inventory, attributes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (entity_id, name, status, location, _json_text(inventory), _json_text(attributes))
                    for entity_id, name, status, location, inventory, attributes in SEED_ENTITIES
                ),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO world_rules
                    (rule_id, category, forbidden_action, description)
                VALUES (?, ?, ?, ?)
                """,
                SEED_RULES,
            )


def list_entities(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    """Return entities in a UI-friendly representation."""
    initialize_database(database_path, seed=False)
    with database(database_path) as connection:
        rows = connection.execute(
            "SELECT id, name, status, location, inventory, attributes FROM entities ORDER BY name"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["inventory"] = json.loads(item["inventory"])
        item["attributes"] = json.loads(item["attributes"] or "{}")
        result.append(item)
    return result


def get_entity(name: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> dict[str, Any] | None:
    """Find an entity by its case-insensitive display name."""
    initialize_database(database_path, seed=False)
    with database(database_path) as connection:
        row = connection.execute(
            "SELECT id, name, status, location, inventory, attributes FROM entities WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["inventory"] = json.loads(item["inventory"])
    item["attributes"] = json.loads(item["attributes"] or "{}")
    return item


def list_rules(database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    """Return enabled world rules."""
    initialize_database(database_path, seed=False)
    with database(database_path) as connection:
        rows = connection.execute(
            "SELECT rule_id, category, forbidden_action, description FROM world_rules ORDER BY rule_id"
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_entity(
    entity_id: str,
    name: str,
    status: str,
    location: str,
    inventory: list[str],
    attributes: dict[str, Any],
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Insert or update an entity after validating JSON-shaped fields."""
    if status not in {"alive", "dead", "unconscious", "injured", "missing"}:
        raise ValueError(f"Unsupported entity status: {status}")
    if not name.strip() or not location.strip():
        raise ValueError("Entity name and location are required")
    with database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO entities (id, name, status, location, inventory, attributes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                status = excluded.status,
                location = excluded.location,
                inventory = excluded.inventory,
                attributes = excluded.attributes
            """,
            (entity_id, name.strip(), status, location.strip(), _json_text(inventory), _json_text(attributes)),
        )
