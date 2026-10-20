#!/usr/bin/env python3
"""Migration transactionnelle de scans.db (SQLite) vers PostgreSQL.

Utilisation recommandee dans le conteneur API :
  python /app/scripts/migrate_sqlite_to_postgres.py \
    --sqlite /app/scans.db \
    --postgres "$DATABASE_URL" \
    --reset-target \
    --verify

Le script :
- lit uniquement les tables connues de qr-ocr-app ;
- preserve les identifiants, dates, textes et BLOB d'images ;
- execute la migration dans une transaction PostgreSQL ;
- remet les sequences d'identifiants au bon niveau ;
- compare les nombres de lignes source/cible ;
- annule toute la transaction en cas d'erreur.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

# Ordre parent -> enfant. TRUNCATE utilise CASCADE, mais l'ordre reste utile
# pour l'insertion et la lecture des journaux.
TABLES = [
    "appsecret",
    "useraccount",
    "scan",
    "stockitem",
    "patient",
    "scanchange",
    "auditevent",
    "stockqrunit",
    "stockwithdrawal",
    "stockqraddition",
    "patientdispensation",
]


def safe_dsn(dsn: str) -> str:
    """Masque le mot de passe pour les messages."""
    try:
        parts = urlsplit(dsn)
        if parts.password is None:
            return dsn
        username = parts.username or ""
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{username}:***@{host}{port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return "<DSN PostgreSQL masque>"


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def postgres_tables(conn: psycopg.Connection[Any]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        return {str(row[0]) for row in cur.fetchall()}


def postgres_columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def postgres_column_types(
    conn: psycopg.Connection[Any], table: str
) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return {str(name): str(data_type) for name, data_type in cur.fetchall()}


def convert_value(value: Any, pg_type: str) -> Any:
    if value is None:
        return None
    if pg_type == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "t", "yes", "y"}
        return bool(value)
    if pg_type == "date" and isinstance(value, str):
        return date.fromisoformat(value[:10])
    if pg_type.startswith("timestamp") and isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    return value


def count_sqlite(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def count_postgres(conn: psycopg.Connection[Any], table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
        return int(cur.fetchone()[0])


def chunks(rows: Iterable[sqlite3.Row], size: int) -> Iterable[list[sqlite3.Row]]:
    batch: list[sqlite3.Row] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def reset_target(conn: psycopg.Connection[Any], existing_tables: list[str]) -> None:
    if not existing_tables:
        return
    statement = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
        sql.SQL(", ").join(sql.Identifier(name) for name in existing_tables)
    )
    with conn.cursor() as cur:
        cur.execute(statement)


def target_is_empty(conn: psycopg.Connection[Any], tables: list[str]) -> bool:
    return all(count_postgres(conn, table) == 0 for table in tables)


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg.Connection[Any],
    table: str,
    batch_size: int,
) -> int:
    source_columns = sqlite_columns(sqlite_conn, table)
    target_types = postgres_column_types(pg_conn, table)
    target_column_set = set(target_types)
    columns = [name for name in source_columns if name in target_column_set]

    if not columns:
        raise RuntimeError(f"Aucune colonne commune pour la table {table}.")

    missing_in_target = sorted(set(source_columns) - target_column_set)
    if missing_in_target:
        print(
            f"AVERTISSEMENT {table}: colonnes SQLite ignorees car absentes de PostgreSQL: "
            + ", ".join(missing_in_target),
            file=sys.stderr,
        )

    select_sql = (
        "SELECT "
        + ", ".join(f'"{name}"' for name in columns)
        + f' FROM "{table}"'
    )
    source_cursor = sqlite_conn.execute(select_sql)

    insert_statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(name) for name in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )

    migrated = 0
    with pg_conn.cursor() as pg_cursor:
        for batch in chunks(source_cursor, batch_size):
            values = [
                tuple(convert_value(row[name], target_types[name]) for name in columns)
                for row in batch
            ]
            pg_cursor.executemany(insert_statement, values)
            migrated += len(values)
    return migrated


def reset_sequences(conn: psycopg.Connection[Any], tables: list[str]) -> None:
    """Replace chaque sequence serial/identity sur MAX(id), si applicable."""
    with conn.cursor() as cur:
        for table in tables:
            columns = postgres_columns(conn, table)
            if "id" not in columns:
                continue
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (f"public.{table}",))
            sequence_row = cur.fetchone()
            sequence_name = sequence_row[0] if sequence_row else None
            if not sequence_name:
                continue
            cur.execute(
                sql.SQL("SELECT COALESCE(MAX(id), 0) FROM {}").format(sql.Identifier(table))
            )
            maximum = int(cur.fetchone()[0])
            if maximum > 0:
                cur.execute("SELECT setval(%s, %s, true)", (sequence_name, maximum))
            else:
                cur.execute("SELECT setval(%s, 1, false)", (sequence_name,))


def verify_counts(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg.Connection[Any],
    tables: list[str],
) -> bool:
    ok = True
    print("\nVerification des nombres de lignes")
    print("-" * 58)
    for table in tables:
        source_count = count_sqlite(sqlite_conn, table)
        target_count = count_postgres(pg_conn, table)
        status = "OK" if source_count == target_count else "ECHEC"
        print(f"{table:22} SQLite={source_count:6} PostgreSQL={target_count:6} {status}")
        ok = ok and source_count == target_count
    return ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="Chemin du fichier scans.db")
    parser.add_argument("--postgres", required=True, help="URL postgresql+psycopg:// ou postgresql://")
    parser.add_argument(
        "--reset-target",
        action="store_true",
        help="Vide les tables PostgreSQL avant import. Necessaire si l'admin initial a deja ete cree.",
    )
    parser.add_argument("--verify", action="store_true", help="Compare les nombres de lignes apres import")
    parser.add_argument("--batch-size", type=int, default=500, help="Taille des lots d'insertion")
    return parser.parse_args()


def normalize_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn.removeprefix("postgresql+psycopg://")
    return dsn


def main() -> int:
    args = parse_args()
    sqlite_path = Path(args.sqlite).expanduser().resolve()
    if not sqlite_path.is_file():
        print(f"ERREUR: fichier SQLite introuvable: {sqlite_path}", file=sys.stderr)
        return 2
    if args.batch_size < 1:
        print("ERREUR: --batch-size doit etre positif.", file=sys.stderr)
        return 2

    dsn = normalize_dsn(args.postgres)
    print(f"Source SQLite : {sqlite_path}")
    print(f"Cible PostgreSQL : {safe_dsn(dsn)}")

    sqlite_conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    sqlite_conn.row_factory = sqlite3.Row

    try:
        source_available = sqlite_tables(sqlite_conn)
        source_tables = [table for table in TABLES if table in source_available]
        if not source_tables:
            raise RuntimeError("Aucune table attendue n'a ete trouvee dans SQLite.")

        missing_source = [table for table in TABLES if table not in source_available]
        if missing_source:
            print(
                "AVERTISSEMENT: tables absentes de SQLite et donc non migrees: "
                + ", ".join(missing_source),
                file=sys.stderr,
            )

        with psycopg.connect(dsn) as pg_conn:
            target_available = postgres_tables(pg_conn)
            missing_target = [table for table in source_tables if table not in target_available]
            if missing_target:
                raise RuntimeError(
                    "Tables absentes de PostgreSQL: " + ", ".join(missing_target)
                )

            print("Debut de la transaction PostgreSQL.")
            if args.reset_target:
                print("Vidage des tables cibles avec RESTART IDENTITY CASCADE.")
                reset_target(pg_conn, source_tables)
            elif not target_is_empty(pg_conn, source_tables):
                raise RuntimeError(
                    "La cible PostgreSQL n'est pas vide. Relancez avec --reset-target "
                    "si vous voulez remplacer son contenu."
                )

            for table in source_tables:
                migrated = migrate_table(
                    sqlite_conn, pg_conn, table, args.batch_size
                )
                print(f"{table:22} {migrated:6} ligne(s) migree(s)")

            reset_sequences(pg_conn, source_tables)

            if args.verify and not verify_counts(sqlite_conn, pg_conn, source_tables):
                raise RuntimeError("La verification des nombres de lignes a echoue.")

            # Le context manager psycopg valide ici. Toute exception provoque un rollback.
            print("\nMigration validee. Transaction COMMIT.")
        return 0

    except Exception as error:
        print(f"\nERREUR: {error}", file=sys.stderr)
        print("La transaction PostgreSQL a ete annulee.", file=sys.stderr)
        return 1
    finally:
        sqlite_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
