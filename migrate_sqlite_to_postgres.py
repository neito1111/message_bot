import asyncio
import os
import sqlite3
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.sql.sqltypes import DateTime


TABLES = [
    "users",
    "tg_accounts",
    "access_requests",
    "dm_profiles",
    "chats",
    "event_sequences",
    "events",
]


def load_sqlite_rows(sqlite_path: str) -> dict[str, list[dict]]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    data: dict[str, list[dict]] = {}
    try:
        for table in TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(row) for row in rows]
    finally:
        conn.close()
    return data


def normalize_value(column, value):
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


async def main() -> None:
    sqlite_path = os.getenv("SQLITE_PATH", "./message_bot.db")
    target_db_url = os.getenv("TARGET_DB_URL")
    if not target_db_url:
        raise RuntimeError("TARGET_DB_URL is required")

    os.environ["DB_URL"] = target_db_url

    from bot.db import Base, engine

    sqlite_data = load_sqlite_rows(sqlite_path)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        for table in reversed(TABLES):
            await conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))

        for table in TABLES:
            columns = [column.name for column in Base.metadata.tables[table].columns]
            rows = sqlite_data[table]
            if not rows:
                print(f"{table}: 0 rows")
                continue

            placeholders = ", ".join(f":{column}" for column in columns)
            quoted_columns = ", ".join(columns)
            payload = [
                {
                    column.name: normalize_value(column, row.get(column.name))
                    for column in Base.metadata.tables[table].columns
                }
                for row in rows
            ]
            await conn.execute(
                text(f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders})"),
                payload,
            )
            print(f"{table}: {len(rows)} rows")

        for table in TABLES:
            if "id" not in Base.metadata.tables[table].columns:
                continue
            await conn.execute(
                text(
                    """
                    SELECT setval(
                        pg_get_serial_sequence(:table_name, 'id'),
                        COALESCE((SELECT MAX(id) FROM {}), 1),
                        true
                    )
                    """.format(table)
                ),
                {"table_name": table},
            )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
