from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models import Base

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path.as_posix()}",
    echo=False,
    future=True,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _migrate_schema(connection) -> None:  # type: ignore[no-untyped-def]
    """Small in-place migrations for SQLite databases created by older builds."""
    columns = {
        str(row[1])
        for row in connection.exec_driver_sql("PRAGMA table_info(groups)").fetchall()
    }
    if "account_key" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE groups ADD COLUMN account_key VARCHAR(160) DEFAULT ''"
        )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_groups_account_key ON groups (account_key)"
    )
    config_columns = {
        str(row[1])
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(group_configs)"
        ).fetchall()
    }
    if "read_enabled" not in config_columns:
        connection.exec_driver_sql(
            "ALTER TABLE group_configs ADD COLUMN read_enabled BOOLEAN DEFAULT 0"
        )
    if "read_interval_seconds" not in config_columns:
        connection.exec_driver_sql(
            "ALTER TABLE group_configs ADD COLUMN read_interval_seconds INTEGER DEFAULT 0"
        )
    if "last_memory_message_id" not in config_columns:
        connection.exec_driver_sql(
            "ALTER TABLE group_configs ADD COLUMN last_memory_message_id INTEGER DEFAULT 0"
        )
    if "multi_message_enabled" not in config_columns:
        connection.exec_driver_sql(
            "ALTER TABLE group_configs ADD COLUMN multi_message_enabled BOOLEAN DEFAULT 0"
        )
    if "max_messages" not in config_columns:
        connection.exec_driver_sql(
            "ALTER TABLE group_configs ADD COLUMN max_messages INTEGER DEFAULT 3"
        )
    if "proactive_enabled" not in config_columns:
        connection.exec_driver_sql(
            "ALTER TABLE group_configs ADD COLUMN proactive_enabled BOOLEAN DEFAULT 0"
        )
    if "proactive_interval_minutes" not in config_columns:
        connection.exec_driver_sql(
            "ALTER TABLE group_configs ADD COLUMN proactive_interval_minutes INTEGER DEFAULT 90"
        )
    assistant_columns = {
        str(row[1])
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(assistant_messages)"
        ).fetchall()
    }
    if "kind" not in assistant_columns:
        connection.exec_driver_sql(
            "ALTER TABLE assistant_messages ADD COLUMN kind VARCHAR(24) DEFAULT 'text'"
        )
    if "media_path" not in assistant_columns:
        connection.exec_driver_sql(
            "ALTER TABLE assistant_messages ADD COLUMN media_path VARCHAR(512) DEFAULT ''"
        )
    contact_columns = {
        str(row[1])
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(contacts)"
        ).fetchall()
    }
    if "trigger_mode" not in contact_columns:
        connection.exec_driver_sql(
            "ALTER TABLE contacts ADD COLUMN trigger_mode VARCHAR(24) DEFAULT 'all'"
        )
    if "keywords" not in contact_columns:
        connection.exec_driver_sql(
            "ALTER TABLE contacts ADD COLUMN keywords JSON DEFAULT '[]'"
        )
    if "temperature" not in contact_columns:
        connection.exec_driver_sql(
            "ALTER TABLE contacts ADD COLUMN temperature FLOAT DEFAULT 0.3"
        )
    if "persona" not in contact_columns:
        connection.exec_driver_sql(
            "ALTER TABLE contacts ADD COLUMN persona TEXT DEFAULT ''"
        )
    bili_columns = {
        str(row[1])
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(bili_accounts)"
        ).fetchall()
    }
    if bili_columns and "cookie_json" not in bili_columns:
        connection.exec_driver_sql(
            "ALTER TABLE bili_accounts ADD COLUMN cookie_json TEXT DEFAULT ''"
        )
    # Migrate the old "MVC" naming to "MCP" while preserving toggles.
    legacy_caps = connection.exec_driver_sql(
        "SELECT id, key, type FROM capabilities "
        "WHERE key LIKE 'mvc:%' OR key = 'tool:run_mvc' OR type = 'mvc'"
    ).fetchall()
    for cap_id, key, cap_type in legacy_caps:
        key = str(key or "")
        new_key = key
        if key.startswith("mvc:"):
            new_key = ("mcp:" + key[4:]).replace(
                "screen_control_mvc", "screen_control_mcp"
            )
        elif key == "tool:run_mvc":
            new_key = "tool:run_mcp"
        if new_key != key:
            conflict = connection.exec_driver_sql(
                "SELECT id FROM capabilities WHERE key = ?", (new_key,)
            ).fetchone()
            if conflict:
                connection.exec_driver_sql(
                    "DELETE FROM capabilities WHERE id = ?", (cap_id,)
                )
                continue
            connection.exec_driver_sql(
                "UPDATE capabilities SET key = ?, type = 'mcp' WHERE id = ?",
                (new_key, cap_id),
            )
        elif str(cap_type or "") == "mvc":
            connection.exec_driver_sql(
                "UPDATE capabilities SET type = 'mcp' WHERE id = ?", (cap_id,)
            )


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema)


async def dispose_db() -> None:
    await engine.dispose()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
