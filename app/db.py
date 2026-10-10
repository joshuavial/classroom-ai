"""Postgres connection pool and the migration runner.

Migrations are numbered SQL files in app/schema/ named NNN_description.sql.
They are applied at startup, in order, and recorded in schema_migrations.
"""

import asyncio
import hashlib
import re
from pathlib import Path

from psycopg_pool import AsyncConnectionPool

SCHEMA_DIR = Path(__file__).parent / "schema"
MIGRATION_NAME = re.compile(r"^(\d{3})_.+\.sql$")
# Any fixed number works; it only has to be the same in every app process.
MIGRATION_LOCK_KEY = 7_311_005


class MigrationError(Exception):
    pass


async def open_pool(dsn: str) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(dsn, min_size=1, max_size=10, open=False)
    await pool.open(wait=True, timeout=30)
    return pool


def load_migrations(schema_dir: Path = SCHEMA_DIR) -> list[tuple[int, Path]]:
    found: dict[int, Path] = {}
    for path in sorted(schema_dir.iterdir()):
        if path.name.startswith("."):
            continue  # .DS_Store and the like
        match = MIGRATION_NAME.match(path.name)
        if not match:
            raise MigrationError(f"not a migration file name: {path.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"two migrations numbered {version:03d}")
        found[version] = path
    return sorted(found.items())


async def migrate(pool: AsyncConnectionPool, schema_dir: Path = SCHEMA_DIR) -> list[int]:
    """Apply every migration not yet recorded. Returns the versions applied.

    One transaction, under an advisory lock, so a failure applies nothing and
    two processes starting together cannot both apply the same file. This
    relies on the default READ COMMITTED isolation: a process that waited for
    the lock sees what the first one committed. Migration files therefore
    cannot use statements that refuse to run in a transaction, such as
    CREATE INDEX CONCURRENTLY.
    """
    migrations = load_migrations(schema_dir)
    applied_now: list[int] = []
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version integer PRIMARY KEY,"
            " checksum text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cur = await conn.execute("SELECT version, checksum FROM schema_migrations")
        applied = dict(await cur.fetchall())
        known = {version for version, _ in migrations}
        if unknown := set(applied) - known:
            raise MigrationError(f"database has migrations this code lacks: {sorted(unknown)}")
        for version, path in migrations:
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if version in applied:
                if applied[version] != checksum:
                    raise MigrationError(f"migration {version:03d} changed after it was applied")
                continue
            if applied and version < max(applied):
                raise MigrationError(
                    f"migration {version:03d} is older than applied {max(applied):03d}"
                )
            # No parameters: psycopg runs a multi-statement string only without them.
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, checksum),
            )
            applied[version] = checksum
            applied_now.append(version)
    return applied_now


async def check(pool: AsyncConnectionPool, timeout: float = 2.0) -> bool:
    """True if the database answers within the timeout (seconds)."""

    async def ping() -> None:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")

    try:
        await asyncio.wait_for(ping(), timeout)
        return True
    except Exception:
        return False
