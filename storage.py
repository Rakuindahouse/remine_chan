from __future__ import annotations

import os
import asyncpg
from datetime import datetime
from typing import Optional

_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            os.getenv("DATABASE_URL"),
            ssl="require",
            statement_cache_size=0,
        )
    return _pool


async def init_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id                  SERIAL      PRIMARY KEY,
                guild_id            BIGINT      NOT NULL,
                user_id             BIGINT      NOT NULL,
                message_id          BIGINT,
                message_link        TEXT        NOT NULL,
                task_description    TEXT        NOT NULL,
                remind_at           TIMESTAMP   NOT NULL,
                notified            BOOLEAN     NOT NULL DEFAULT FALSE,
                created_at          TIMESTAMP   NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id            BIGINT  PRIMARY KEY,
                reminder_channel_id BIGINT,
                default_remind_time TEXT    NOT NULL DEFAULT '23:30'
            )
        """)


async def add_reminder(
    *,
    guild_id: int,
    user_id: int,
    message_id: Optional[int],
    message_link: str,
    task_description: str,
    remind_at: datetime,
) -> int:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO reminders
               (guild_id, user_id, message_id, message_link, task_description, remind_at)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING id""",
            guild_id, user_id, message_id, message_link, task_description, remind_at,
        )
        return row["id"]


async def get_due_reminders(now: datetime) -> list:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM reminders WHERE remind_at <= $1 AND notified = FALSE",
            now,
        )


async def mark_notified(reminder_id: int) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reminders SET notified = TRUE WHERE id = $1", reminder_id
        )


async def list_reminders(guild_id: int) -> list:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT * FROM reminders
               WHERE guild_id = $1 AND notified = FALSE
               ORDER BY remind_at""",
            guild_id,
        )


async def delete_reminder(reminder_id: int, guild_id: int) -> bool:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """DELETE FROM reminders
               WHERE id = $1 AND guild_id = $2 AND notified = FALSE""",
            reminder_id, guild_id,
        )
        return result == "DELETE 1"


async def get_guild_config(guild_id: int):
    pool = await _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM guild_config WHERE guild_id = $1", guild_id
        )


async def set_reminder_channel(guild_id: int, channel_id: int) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_config (guild_id, reminder_channel_id)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET reminder_channel_id = $2""",
            guild_id, channel_id,
        )


async def get_default_time(guild_id: int) -> str:
    config = await get_guild_config(guild_id)
    if config and config["default_remind_time"]:
        return config["default_remind_time"]
    return "23:30"


async def set_default_time(guild_id: int, time_str: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_config (guild_id, default_remind_time)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET default_remind_time = $2""",
            guild_id, time_str,
        )
