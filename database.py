"""SQLite layer. One connection, serialized by a lock, run in worker threads.

All SQL is parameterized. `transaction()` gives atomic multi-statement work
(used for waifu claims, trades, cooldowns) so races cannot double-spend.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT, first_name TEXT,
    xp INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 1,
    songs_played INTEGER NOT NULL DEFAULT 0, messages_count INTEGER NOT NULL DEFAULT 0,
    last_xp_at REAL NOT NULL DEFAULT 0, favorite_waifu_id INTEGER,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY, title TEXT,
    prefix TEXT NOT NULL DEFAULT '/', lang TEXT NOT NULL DEFAULT 'en',
    waifu_enabled INTEGER NOT NULL DEFAULT 1, waifu_interval INTEGER NOT NULL DEFAULT 120,
    gacha_enabled INTEGER NOT NULL DEFAULT 1, music_enabled INTEGER NOT NULL DEFAULT 1,
    maintenance INTEGER NOT NULL DEFAULT 0,
    last_activity_at REAL NOT NULL DEFAULT 0, messages_since_spawn INTEGER NOT NULL DEFAULT 0,
    last_spawn_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS economy (
    user_id INTEGER PRIMARY KEY,
    coins INTEGER NOT NULL DEFAULT 500 CHECK (coins >= 0),
    gems INTEGER NOT NULL DEFAULT 300 CHECK (gems >= 0),
    total_coins_earned INTEGER NOT NULL DEFAULT 0, total_gems_earned INTEGER NOT NULL DEFAULT 0,
    gacha_pulls INTEGER NOT NULL DEFAULT 0, pity INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_inventory (
    user_id INTEGER NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, item)
);
CREATE TABLE IF NOT EXISTS waifus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, anime TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '',
    rarity TEXT NOT NULL DEFAULT 'COMMON', aliases TEXT NOT NULL DEFAULT '[]',
    value INTEGER NOT NULL DEFAULT 100, source TEXT NOT NULL DEFAULT 'manual',
    source_id TEXT NOT NULL DEFAULT '', release_info TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', popularity INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE (source, source_id)
);
CREATE TABLE IF NOT EXISTS user_waifus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, waifu_id INTEGER NOT NULL REFERENCES waifus(id),
    favorite INTEGER NOT NULL DEFAULT 0, obtained_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS banners (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    pool_tag TEXT NOT NULL DEFAULT '', rate_multipliers TEXT NOT NULL DEFAULT '{}',
    cost_gems INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gacha_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, banner_id TEXT NOT NULL,
    waifu_id INTEGER NOT NULL, rarity TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS daily_rewards (
    user_id INTEGER PRIMARY KEY, last_daily REAL NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0, last_weekly REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS music_queues (
    chat_id INTEGER NOT NULL, position INTEGER NOT NULL, track_json TEXT NOT NULL,
    PRIMARY KEY (chat_id, position)
);
CREATE TABLE IF NOT EXISTS music_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '', played_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS waifu_guesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    spawn_id INTEGER, guess TEXT NOT NULL DEFAULT '', correct INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS active_spawns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, waifu_id INTEGER NOT NULL,
    message_id INTEGER, spawned_at REAL NOT NULL, claimed_by INTEGER, claimed_at REAL
);
CREATE TABLE IF NOT EXISTS cooldowns (
    user_id INTEGER NOT NULL, key TEXT NOT NULL, expires_at REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS achievements (
    user_id INTEGER NOT NULL, achievement_id TEXT NOT NULL,
    unlocked_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (user_id, achievement_id)
);
CREATE TABLE IF NOT EXISTS quests (
    user_id INTEGER NOT NULL, quest_id TEXT NOT NULL, day TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, quest_id, day)
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, from_user INTEGER NOT NULL, to_user INTEGER NOT NULL,
    give_instance INTEGER NOT NULL, want_instance INTEGER, status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS marriages (
    user_id INTEGER PRIMARY KEY, partner_id INTEGER NOT NULL,
    married_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS api_cache (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS image_cache (
    url TEXT PRIMARY KEY, file_id TEXT NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_image_cache_upd ON image_cache(updated);
CREATE INDEX IF NOT EXISTS idx_user_waifus_user ON user_waifus(user_id);
CREATE INDEX IF NOT EXISTS idx_user_waifus_waifu ON user_waifus(waifu_id);
CREATE INDEX IF NOT EXISTS idx_waifus_rarity ON waifus(rarity, enabled);
CREATE INDEX IF NOT EXISTS idx_gacha_user ON gacha_history(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_history_chat ON music_history(chat_id, played_at);
CREATE INDEX IF NOT EXISTS idx_history_user ON music_history(user_id, played_at);
CREATE INDEX IF NOT EXISTS idx_guesses_chat ON waifu_guesses(chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_spawns_chat ON active_spawns(chat_id, claimed_by);
CREATE INDEX IF NOT EXISTS idx_cooldowns_exp ON cooldowns(expires_at);
CREATE INDEX IF NOT EXISTS idx_trades_users ON trades(from_user, to_user, status);
CREATE INDEX IF NOT EXISTS idx_users_xp ON users(xp);
CREATE INDEX IF NOT EXISTS idx_api_cache_exp ON api_cache(expires_at);
"""

DEFAULT_BANNERS = [
    ("anime_dreams", "ANIME DREAMS BANNER", "Increased SSR rates", "", '{"SSR": 3.0, "UR": 2.0}', 100),
    ("battle", "BATTLE BANNER", "Combat-themed characters", "battle", '{"EPIC": 1.5, "LEGENDARY": 1.5}', 100),
    ("classic", "CLASSIC BANNER", "Mixed characters", "", "{}", 80),
]


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # ---- lifecycle -------------------------------------------------
    async def connect(self) -> None:
        await asyncio.to_thread(self._open)

    def _open(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        for b in DEFAULT_BANNERS:
            conn.execute(
                "INSERT OR IGNORE INTO banners (id,name,description,pool_tag,rate_multipliers,cost_gems) "
                "VALUES (?,?,?,?,?,?)", b)
        self._conn = conn

    async def close(self) -> None:
        def _close() -> None:
            with self._lock:
                if self._conn:
                    self._conn.close()
                    self._conn = None
        await asyncio.to_thread(_close)

    # ---- primitives ------------------------------------------------
    def _run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            assert self._conn is not None, "Database not connected"
            return fn(self._conn)

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        def op(c: sqlite3.Connection) -> int:
            return c.execute(sql, tuple(params)).rowcount
        return await asyncio.to_thread(self._run, op)

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        def op(c: sqlite3.Connection) -> dict | None:
            row = c.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None
        return await asyncio.to_thread(self._run, op)

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        def op(c: sqlite3.Connection) -> list[dict]:
            return [dict(r) for r in c.execute(sql, tuple(params)).fetchall()]
        return await asyncio.to_thread(self._run, op)

    async def transaction(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """Run sync `fn(conn)` atomically (BEGIN IMMEDIATE .. COMMIT)."""
        def op(c: sqlite3.Connection) -> Any:
            c.execute("BEGIN IMMEDIATE")
            try:
                result = fn(c)
                c.execute("COMMIT")
                return result
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return await asyncio.to_thread(self._run, op)

    # ---- users / groups --------------------------------------------
    async def ensure_user(self, user_id: int, username: str | None = None, first_name: str | None = None) -> None:
        def op(c: sqlite3.Connection) -> None:
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            c.execute("INSERT OR IGNORE INTO economy (user_id) VALUES (?)", (user_id,))
            c.execute("INSERT OR IGNORE INTO daily_rewards (user_id) VALUES (?)", (user_id,))
            if username is not None or first_name is not None:
                c.execute("UPDATE users SET username=COALESCE(?,username), first_name=COALESCE(?,first_name) "
                          "WHERE user_id=?", (username, first_name, user_id))
        await self.transaction(op)

    async def ensure_group(self, chat_id: int, title: str | None = None,
                           default_interval: int = 120, default_enabled: bool = True) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO groups (chat_id,title,waifu_interval,waifu_enabled) VALUES (?,?,?,?)",
            (chat_id, title, default_interval, int(default_enabled)))
        if title:
            await self.execute("UPDATE groups SET title=? WHERE chat_id=?", (title, chat_id))

    # ---- cooldowns -------------------------------------------------
    async def check_cooldown(self, user_id: int, key: str, seconds: float) -> float:
        """Atomic. Returns 0 and arms the cooldown if free, else seconds remaining."""
        def op(c: sqlite3.Connection) -> float:
            now = time.time()
            row = c.execute("SELECT expires_at FROM cooldowns WHERE user_id=? AND key=?",
                            (user_id, key)).fetchone()
            if row and row["expires_at"] > now:
                return row["expires_at"] - now
            c.execute("INSERT INTO cooldowns (user_id,key,expires_at) VALUES (?,?,?) "
                      "ON CONFLICT(user_id,key) DO UPDATE SET expires_at=excluded.expires_at",
                      (user_id, key, now + seconds))
            return 0.0
        return await self.transaction(op)

    async def purge_expired(self) -> None:
        now = time.time()
        await self.execute("DELETE FROM cooldowns WHERE expires_at < ?", (now,))
        await self.execute("DELETE FROM api_cache WHERE expires_at < ?", (now,))

    # ---- music persistence -----------------------------------------
    async def save_queue(self, chat_id: int, tracks: list[dict]) -> None:
        def op(c: sqlite3.Connection) -> None:
            c.execute("DELETE FROM music_queues WHERE chat_id=?", (chat_id,))
            c.executemany("INSERT INTO music_queues (chat_id,position,track_json) VALUES (?,?,?)",
                          [(chat_id, i, json.dumps(t)) for i, t in enumerate(tracks)])
        await self.transaction(op)

    async def load_queue(self, chat_id: int) -> list[dict]:
        rows = await self.fetchall(
            "SELECT track_json FROM music_queues WHERE chat_id=? ORDER BY position", (chat_id,))
        out = []
        for r in rows:
            try:
                out.append(json.loads(r["track_json"]))
            except ValueError:
                log.warning("Skipping corrupt queue row for chat %s", chat_id)
        return out

    async def add_history(self, chat_id: int, user_id: int, title: str, artist: str,
                          source: str, source_id: str) -> None:
        def op(c: sqlite3.Connection) -> None:
            c.execute("INSERT INTO music_history (chat_id,user_id,title,artist,source,source_id) "
                      "VALUES (?,?,?,?,?,?)", (chat_id, user_id, title, artist, source, source_id))
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            c.execute("UPDATE users SET songs_played=songs_played+1 WHERE user_id=?", (user_id,))
        await self.transaction(op)
