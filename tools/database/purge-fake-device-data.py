#!/usr/bin/env python3
"""Plan or execute removal of fake device assets and their dependent rows.

Run with Python 3.11 and an ephemeral connector dependency, for example:

    $env:ECOBIN_PURGE_MYSQL_PASSWORD = '<read from a local secret store>'
    uv run --python 3.11 --with mysql-connector-python python \
      tools/database/purge-fake-device-data.py \
      --database ecobin --keep-hardware-sn test-divice-1

The default mode is read-only. Execution additionally requires a readable
backup evidence file and an exact confirmation phrase. Passwords are read only
from ECOBIN_PURGE_MYSQL_PASSWORD and are never printed.
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import mysql.connector


IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
IMMUTABLE_DELETE_TABLES = {"rec_clean_record_change"}


@dataclass(frozen=True)
class ForeignKey:
    child_table: str
    child_columns: tuple[str, ...]
    parent_table: str
    parent_columns: tuple[str, ...]


def quote_identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise RuntimeError(f"unsafe database identifier: {value!r}")
    return f"`{value}`"


def chunks(values: Iterable[tuple[Any, ...]], size: int = 200):
    batch: list[tuple[Any, ...]] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def tuple_predicate(columns: tuple[str, ...], row_count: int) -> str:
    quoted = [quote_identifier(column) for column in columns]
    if len(columns) == 1:
        return f"{quoted[0]} IN ({','.join(['%s'] * row_count)})"
    row = "(" + ",".join(["%s"] * len(columns)) + ")"
    return f"({','.join(quoted)}) IN ({','.join([row] * row_count)})"


def flatten(rows: Iterable[tuple[Any, ...]]) -> list[Any]:
    return [value for row in rows for value in row]


class PurgePlanner:
    def __init__(self, connection, database: str, keep_hardware_sn: str):
        self.connection = connection
        self.database = database
        self.keep_hardware_sn = keep_hardware_sn
        self.primary_keys = self._load_primary_keys()
        self.columns = self._load_columns()
        self.foreign_keys = self._load_foreign_keys()
        self.selected: dict[str, set[tuple[Any, ...]]] = collections.defaultdict(set)

    def _load_primary_keys(self) -> dict[str, tuple[str, ...]]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.key_column_usage
            WHERE table_schema = %s
              AND constraint_name = 'PRIMARY'
            ORDER BY table_name, ordinal_position
            """,
            (self.database,),
        )
        result: dict[str, list[str]] = collections.defaultdict(list)
        for table, column in cursor:
            result[table].append(column)
        cursor.close()
        return {table: tuple(value) for table, value in result.items()}

    def _load_columns(self) -> dict[str, set[str]]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = %s
            """,
            (self.database,),
        )
        result: dict[str, set[str]] = collections.defaultdict(set)
        for table, column in cursor:
            result[table].add(column)
        cursor.close()
        return dict(result)

    def _load_foreign_keys(self) -> list[ForeignKey]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT
                constraint_name,
                table_name,
                column_name,
                referenced_table_name,
                referenced_column_name
            FROM information_schema.key_column_usage
            WHERE table_schema = %s
              AND referenced_table_name IS NOT NULL
            ORDER BY table_name, constraint_name, ordinal_position
            """,
            (self.database,),
        )
        grouped: dict[tuple[str, str, str], list[tuple[str, str]]] = (
            collections.defaultdict(list)
        )
        for constraint, child, child_column, parent, parent_column in cursor:
            grouped[(constraint, child, parent)].append(
                (child_column, parent_column)
            )
        cursor.close()
        return [
            ForeignKey(
                child_table=child,
                child_columns=tuple(pair[0] for pair in pairs),
                parent_table=parent,
                parent_columns=tuple(pair[1] for pair in pairs),
            )
            for (_, child, parent), pairs in grouped.items()
        ]

    def _select_primary_keys(
        self,
        table: str,
        where: str,
        parameters: Iterable[Any],
    ) -> int:
        primary_key = self.primary_keys.get(table)
        if not primary_key:
            raise RuntimeError(f"table {table} has no primary key")
        sql = (
            "SELECT "
            + ",".join(quote_identifier(column) for column in primary_key)
            + f" FROM {quote_identifier(table)} WHERE {where}"
        )
        cursor = self.connection.cursor()
        cursor.execute(sql, tuple(parameters))
        before = len(self.selected[table])
        self.selected[table].update(tuple(row) for row in cursor)
        cursor.close()
        return len(self.selected[table]) - before

    def _values_for_selected(
        self,
        table: str,
        columns: tuple[str, ...],
    ) -> set[tuple[Any, ...]]:
        primary_key = self.primary_keys[table]
        result: set[tuple[Any, ...]] = set()
        for batch in chunks(sorted(self.selected[table], key=repr)):
            predicate = tuple_predicate(primary_key, len(batch))
            sql = (
                "SELECT "
                + ",".join(quote_identifier(column) for column in columns)
                + f" FROM {quote_identifier(table)} WHERE {predicate}"
            )
            cursor = self.connection.cursor()
            cursor.execute(sql, flatten(batch))
            result.update(tuple(row) for row in cursor)
            cursor.close()
        return result

    def _select_children(self, foreign_key: ForeignKey) -> int:
        if not self.selected.get(foreign_key.parent_table):
            return 0
        parent_values = self._values_for_selected(
            foreign_key.parent_table,
            foreign_key.parent_columns,
        )
        added = 0
        for batch in chunks(sorted(parent_values, key=repr)):
            predicate = tuple_predicate(
                foreign_key.child_columns,
                len(batch),
            )
            added += self._select_primary_keys(
                foreign_key.child_table,
                predicate,
                flatten(batch),
            )
        return added

    def _select_source_inboxes(self) -> int:
        if "ops_inbox_message" not in self.primary_keys:
            return 0
        inbox_ids: set[tuple[Any, ...]] = set()
        for table, rows in list(self.selected.items()):
            if not rows or "source_inbox_id" not in self.columns.get(table, set()):
                continue
            for value in self._values_for_selected(table, ("source_inbox_id",)):
                if value[0] is not None:
                    inbox_ids.add(value)
        before = len(self.selected["ops_inbox_message"])
        self.selected["ops_inbox_message"].update(inbox_ids)
        return len(self.selected["ops_inbox_message"]) - before

    def build(self) -> tuple[list[str], list[str]]:
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT hardware_sn FROM dev_device_asset ORDER BY hardware_sn"
        )
        hardware = [row[0] for row in cursor]
        cursor.close()
        if hardware.count(self.keep_hardware_sn) != 1:
            raise RuntimeError(
                "retained hardware must exist exactly once; refusing to plan deletion"
            )
        fake_hardware = [value for value in hardware if value != self.keep_hardware_sn]
        if not fake_hardware:
            return hardware, fake_hardware

        placeholders = ",".join(["%s"] * len(fake_hardware))
        self._select_primary_keys(
            "dev_device_asset",
            f"hardware_sn IN ({placeholders})",
            fake_hardware,
        )
        if "ops_inbox_message" in self.columns:
            self._select_primary_keys(
                "ops_inbox_message",
                "source_namespace LIKE 'onenet.%' AND "
                "JSON_UNQUOTE(JSON_EXTRACT(normalized_payload, "
                "'$.trustedSource.deviceName')) "
                f"IN ({placeholders})",
                fake_hardware,
            )

        while True:
            added = 0
            for foreign_key in self.foreign_keys:
                added += self._select_children(foreign_key)
            added += self._select_source_inboxes()
            if added == 0:
                break
        return hardware, fake_hardware

    def total_rows(self) -> int:
        return sum(len(rows) for rows in self.selected.values())

    def print_report(self, fake_hardware: list[str]) -> None:
        print(f"retain hardware: {self.keep_hardware_sn}")
        print(f"fake assets: {len(fake_hardware)}")
        for hardware_sn in fake_hardware:
            print(f"  - {hardware_sn}")
        print("dependent rows selected:")
        for table in sorted(self.selected):
            count = len(self.selected[table])
            if count:
                print(f"  {table}: {count}")
        print(f"total selected rows: {self.total_rows()}")

    def _delete_selected(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
        cursor.close()
        for table in sorted(self.selected):
            primary_key = self.primary_keys[table]
            for batch in chunks(sorted(self.selected[table], key=repr)):
                predicate = tuple_predicate(primary_key, len(batch))
                cursor = self.connection.cursor()
                cursor.execute(
                    f"DELETE FROM {quote_identifier(table)} WHERE {predicate}",
                    flatten(batch),
                )
                if cursor.rowcount != len(batch):
                    raise RuntimeError(
                        f"delete count changed for {table}: "
                        f"expected {len(batch)}, got {cursor.rowcount}"
                    )
                cursor.close()
        cursor = self.connection.cursor()
        cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 1")
        cursor.close()

    def _assert_no_orphans(self) -> None:
        for foreign_key in self.foreign_keys:
            child_alias = "c"
            parent_alias = "p"
            join = " AND ".join(
                f"{child_alias}.{quote_identifier(child)} = "
                f"{parent_alias}.{quote_identifier(parent)}"
                for child, parent in zip(
                    foreign_key.child_columns,
                    foreign_key.parent_columns,
                    strict=True,
                )
            )
            populated = " AND ".join(
                f"{child_alias}.{quote_identifier(column)} IS NOT NULL"
                for column in foreign_key.child_columns
            )
            missing = (
                f"{parent_alias}.{quote_identifier(foreign_key.parent_columns[0])} "
                "IS NULL"
            )
            cursor = self.connection.cursor()
            cursor.execute(
                f"SELECT COUNT(*) FROM "
                f"{quote_identifier(foreign_key.child_table)} {child_alias} "
                f"LEFT JOIN {quote_identifier(foreign_key.parent_table)} "
                f"{parent_alias} ON {join} WHERE {populated} AND {missing}"
            )
            count = int(cursor.fetchone()[0])
            cursor.close()
            if count:
                raise RuntimeError(
                    "orphan validation failed for "
                    f"{foreign_key.child_table} -> {foreign_key.parent_table}"
                )

    def execute(self, maximum_rows: int) -> None:
        if self.total_rows() > maximum_rows:
            raise RuntimeError(
                f"selected {self.total_rows()} rows, above limit {maximum_rows}"
            )
        blocked = [
            table
            for table in IMMUTABLE_DELETE_TABLES
            if self.selected.get(table)
        ]
        if blocked:
            raise RuntimeError(
                "selected immutable audit rows require a database rebuild: "
                + ", ".join(blocked)
            )
        self._delete_selected()
        self._assert_no_orphans()
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT hardware_sn FROM dev_device_asset ORDER BY hardware_sn"
        )
        remaining = [row[0] for row in cursor]
        cursor.close()
        if remaining != [self.keep_hardware_sn]:
            raise RuntimeError(
                f"post-delete asset set is unexpected: {remaining!r}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="ecobin_schema_owner")
    parser.add_argument("--keep-hardware-sn", default="test-divice-1")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup-evidence", type=Path)
    parser.add_argument("--confirmation")
    parser.add_argument("--maximum-rows", type=int, default=100_000)
    return parser.parse_args()


def validate_backup_evidence(path: Path | None) -> None:
    if path is None:
        raise RuntimeError(
            "execution requires readable, non-empty backup evidence"
        )
    try:
        with path.open("rb") as evidence:
            if not evidence.read(1):
                raise RuntimeError(
                    "backup evidence must be readable and non-empty"
                )
    except OSError as exception:
        raise RuntimeError(
            "backup evidence must be readable and non-empty"
        ) from exception


def main() -> int:
    args = parse_args()
    if not IDENTIFIER.fullmatch(args.database):
        raise RuntimeError("database must be a simple MySQL identifier")
    password = os.environ.get("ECOBIN_PURGE_MYSQL_PASSWORD")
    if not password:
        raise RuntimeError("ECOBIN_PURGE_MYSQL_PASSWORD is required")
    if args.execute:
        expected = f"DELETE-ALL-EXCEPT-{args.keep_hardware_sn}"
        if args.confirmation != expected:
            raise RuntimeError(f"execution requires --confirmation {expected}")
        validate_backup_evidence(args.backup_evidence)

    connection = mysql.connector.connect(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
        password=password,
        autocommit=False,
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        cursor.close()
        if not str(version).startswith("8.4."):
            raise RuntimeError(f"MySQL 8.4.x is required, got {version}")
        connection.rollback()
        connection.start_transaction(isolation_level="SERIALIZABLE")
        planner = PurgePlanner(
            connection,
            args.database,
            args.keep_hardware_sn,
        )
        _, fake_hardware = planner.build()
        planner.print_report(fake_hardware)
        if not args.execute:
            connection.rollback()
            print("dry-run only: no rows were changed")
            return 0
        planner.execute(args.maximum_rows)
        connection.commit()
        print("execution committed after foreign-key and retained-asset validation")
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"purge aborted: {error}", file=sys.stderr)
        raise SystemExit(1)
