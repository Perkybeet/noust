# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_store_migrations.py`` that other tests build on."""

import sqlite3
from pathlib import Path

from tests.support.store import V1_SCHEMA_SQL, V2_DEPLOYMENTS_SQL


def _create_v3_database(db_path: Path) -> None:
    """
    Create a real v3 database with one app, as a 1.4.x release left it.

    Args:
        db_path: Where the database file is created.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V1_SCHEMA_SQL)
        conn.executescript(V2_DEPLOYMENTS_SQL)
        conn.execute("ALTER TABLE apps ADD COLUMN webhook_secret TEXT")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.execute(
            "INSERT INTO apps (domain, app_type, app_path) VALUES (?, ?, ?)",
            ("v3.example.com", "nextjs", "/var/www/apps/v3-example-com"),
        )
        conn.commit()
    finally:
        conn.close()
