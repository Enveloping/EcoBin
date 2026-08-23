from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from edge_store import CURRENT_SCHEMA_VERSION, EdgeStore
from edge_store_prepare import main


def _schema(path: Path) -> tuple[int, bool]:
    connection = sqlite3.connect(path)
    try:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        seal_table = connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name='factory_seal_authorization'"""
        ).fetchone()
        return version, seal_table is not None
    finally:
        connection.close()


@pytest.mark.parametrize("precreate_empty_file", [False, True])
def test_empty_or_absent_database_is_prepared_before_first_boot(
    tmp_path: Path,
    precreate_empty_file: bool,
) -> None:
    database = tmp_path / "edge.db"
    if precreate_empty_file:
        database.touch()

    assert main(["--database", str(database)]) == 0

    assert _schema(database) == (CURRENT_SCHEMA_VERSION, True)


def test_v14_database_is_upgraded_by_schema_only_bootstrap(tmp_path: Path) -> None:
    database = tmp_path / "edge.db"
    store = EdgeStore(str(database))
    store.initialize()
    store._conn.execute("DROP TABLE factory_seal_authorization")
    store._conn.execute("DELETE FROM schema_version WHERE version >= 15")
    store._conn.commit()
    store.close()

    EdgeStore(str(database)).prepare_schema()

    assert _schema(database) == (CURRENT_SCHEMA_VERSION, True)


def test_interrupted_schema_transaction_recovers_on_next_boot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "edge.db"

    def interrupted(migrating: EdgeStore) -> None:
        migrating._conn.execute(
            "CREATE TABLE factory_seal_authorization (command_uid TEXT)"
        )
        raise RuntimeError("injected power loss before migration commit")

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(EdgeStore, "_migrate_v15", interrupted)
        with pytest.raises(RuntimeError, match="injected power loss"):
            EdgeStore(str(database)).prepare_schema()

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert "factory_seal_authorization" not in tables

    EdgeStore(str(database)).prepare_schema()
    assert _schema(database) == (CURRENT_SCHEMA_VERSION, True)
