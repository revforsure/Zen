"""Virtual economy: coins, gems, XP/levels, daily/weekly/work, payments, leaderboards.
Only virtual currency exists. Every balance change happens inside a DB transaction."""
from __future__ import annotations

import math
import random
import sqlite3
import time

DAY = 86400


class EconomyError(Exception):
    """Message is safe to show to users."""


class OnCooldown(EconomyError):
    def __init__(self, seconds: float):
        super().__init__(f"Try again in {fmt_wait(seconds)}.")
        self.seconds = seconds


def fmt_wait(seconds: float) -> str:
    seconds = int(max(1, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return " ".join(p for p in (f"{h}h" if h else "", f"{m}m" if m else "", f"{s}s" if s and not h else "") if p)


def level_for_xp(xp: int) -> int:
    return math.isqrt(max(0, xp) // 100) + 1


def xp_for_level(level: int) -> int:
    return 100 * (level - 1) ** 2


# ---- sync helpers (call inside Database.transaction) -------------------
def add_currency(c: sqlite3.Connection, user_id: int, coins: int = 0, gems: int = 0) -> None:
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    c.execute("INSERT OR IGNORE INTO economy (user_id) VALUES (?)", (user_id,))
    try:
        c.execute(
            "UPDATE economy SET coins=coins+?, gems=gems+?, "
            "total_coins_earned=total_coins_earned+MAX(?,0), total_gems_earned=total_gems_earned+MAX(?,0) "
            "WHERE user_id=?", (coins, gems, coins, gems, user_id))
    except sqlite3.IntegrityError as exc:
        raise EconomyError("Not enough funds.") from exc


def add_xp(c: sqlite3.Connection, user_id: int, amount: int) -> tuple[int, int]:
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    row = c.execute("SELECT xp, level FROM users WHERE user_id=?", (user_id,)).fetchone()
    new_xp = row["xp"] + max(0, amount)
    new_level = level_for_xp(new_xp)
    c.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (new_xp, new_level, user_id))
    if new_level > row["level"]:
        add_currency(c, user_id, coins=100 * (new_level - row["level"]))
    return row["level"], new_level


LEADERBOARDS = {
    "coins": ("SELECT u.user_id, u.username, u.first_name, e.coins AS score FROM economy e "
              "JOIN users u ON u.user_id=e.user_id ORDER BY e.coins DESC, u.user_id LIMIT ? OFFSET ?",
              "SELECT COUNT(*) AS n FROM economy", "💰 Coins"),
    "xp": ("SELECT user_id, username, first_name, xp AS score FROM users ORDER BY xp DESC, user_id LIMIT ? OFFSET ?",
           "SELECT COUNT(*) AS n FROM users", "✨ XP"),
    "waifus": ("SELECT u.user_id, u.username, u.first_name, COUNT(w.id) AS score FROM users u "
               "JOIN user_waifus w ON w.user_id=u.user_id GROUP BY u.user_id "
               "ORDER BY score DESC, u.user_id LIMIT ? OFFSET ?",
               "SELECT COUNT(DISTINCT user_id) AS n FROM user_waifus", "🌸 Waifus"),
    "music": ("SELECT user_id, username, first_name, songs_played AS score FROM users WHERE songs_played>0 "
              "ORDER BY songs_played DESC, user_id LIMIT ? OFFSET ?",
              "SELECT COUNT(*) AS n FROM users WHERE songs_played>0", "🎵 Songs"),
}


class Economy:
    def __init__(self, db, rng: random.Random | None = None):
        self.db = db
        self.rng = rng or random.Random()

    async def balance(self, user_id: int) -> dict:
        await self.db.ensure_user(user_id)
        return await self.db.fetchone("SELECT coins, gems, total_coins_earned, total_gems_earned "
                                      "FROM economy WHERE user_id=?", (user_id,))

    async def grant(self, user_id: int, coins: int = 0, gems: int = 0, xp: int = 0) -> tuple[int, int]:
        await self.db.ensure_user(user_id)

        def op(c):
            add_currency(c, user_id, coins, gems)
            return add_xp(c, user_id, xp) if xp else (0, 0)
        return await self.db.transaction(op)

    async def add_xp(self, user_id: int, amount: int) -> tuple[int, int]:
        await self.db.ensure_user(user_id)
        return await self.db.transaction(lambda c: add_xp(c, user_id, amount))

    # ---- daily / weekly -------------------------------------------
    async def daily(self, user_id: int, now: float | None = None) -> dict:
        await self.db.ensure_user(user_id)
        now = now or time.time()

        def op(c):
            today = int(now // DAY)
            row = c.execute("SELECT last_daily, streak FROM daily_rewards WHERE user_id=?", (user_id,)).fetchone()
            last_day = int(row["last_daily"] // DAY) if row["last_daily"] else None
            if last_day == today:
                raise OnCooldown((today + 1) * DAY - now)
            streak = row["streak"] + 1 if last_day == today - 1 else 1
            coins, gems, xp = 500 + min(streak, 30) * 50, 30, 50
            bonus = streak % 7 == 0
            if bonus:
                coins, gems = coins + 2000, gems + 100
            c.execute("UPDATE daily_rewards SET last_daily=?, streak=? WHERE user_id=?", (now, streak, user_id))
            add_currency(c, user_id, coins, gems)
            old, new = add_xp(c, user_id, xp)
            return {"streak": streak, "coins": coins, "gems": gems, "xp": xp, "bonus": bonus,
                    "level_up": new if new > old else 0}
        return await self.db.transaction(op)

    async def weekly(self, user_id: int, now: float | None = None) -> dict:
        await self.db.ensure_user(user_id)
        now = now or time.time()

        def op(c):
            row = c.execute("SELECT last_weekly FROM daily_rewards WHERE user_id=?", (user_id,)).fetchone()
            if row["last_weekly"] and now - row["last_weekly"] < 7 * DAY:
                raise OnCooldown(row["last_weekly"] + 7 * DAY - now)
            c.execute("UPDATE daily_rewards SET last_weekly=? WHERE user_id=?", (now, user_id))
            add_currency(c, user_id, 3000, 150)
            old, new = add_xp(c, user_id, 200)
            return {"coins": 3000, "gems": 150, "xp": 200, "level_up": new if new > old else 0}
        return await self.db.transaction(op)

    # ---- work / earn ---------------------------------------------
    async def _job(self, user_id: int, key: str, cooldown: int, lo: int, hi: int, xp: int) -> dict:
        await self.db.ensure_user(user_id)
        wait = await self.db.check_cooldown(user_id, key, cooldown)
        if wait:
            raise OnCooldown(wait)
        coins = self.rng.randint(lo, hi)
        old, new = await self.grant(user_id, coins=coins, xp=xp)
        return {"coins": coins, "xp": xp, "level_up": new if new > old else 0}

    async def work(self, user_id: int) -> dict:
        return await self._job(user_id, "work", 3600, 80, 250, 15)

    async def earn(self, user_id: int) -> dict:
        return await self._job(user_id, "earn", 600, 20, 60, 5)

    async def spend(self, user_id: int, coins: int = 0, gems: int = 0) -> None:
        """Atomically remove virtual currency or raise EconomyError."""
        await self.db.ensure_user(user_id)

        def op(c):
            cur = c.execute("UPDATE economy SET coins=coins-?, gems=gems-? WHERE user_id=? AND coins>=? AND gems>=?",
                            (coins, gems, user_id, coins, gems))
            if cur.rowcount != 1:
                raise EconomyError("You don't have enough coins/gems for that.")
        await self.db.transaction(op)

    async def inv_add(self, user_id: int, item: str, qty: int = 1) -> None:
        await self.db.execute("INSERT INTO user_inventory (user_id,item,qty) VALUES (?,?,?) "
                              "ON CONFLICT(user_id,item) DO UPDATE SET qty=qty+excluded.qty", (user_id, item, qty))

    # ---- payments -------------------------------------------------
    async def pay(self, from_id: int, to_id: int, amount: int) -> None:
        if from_id == to_id:
            raise EconomyError("You can't pay yourself.")
        if not 1 <= amount <= 1_000_000_000:
            raise EconomyError("Enter a valid amount.")
        await self.db.ensure_user(from_id)
        await self.db.ensure_user(to_id)

        def op(c):
            cur = c.execute("UPDATE economy SET coins=coins-? WHERE user_id=? AND coins>=?",
                            (amount, from_id, amount))
            if cur.rowcount != 1:
                raise EconomyError("You don't have enough coins.")
            c.execute("UPDATE economy SET coins=coins+? WHERE user_id=?", (amount, to_id))
        await self.db.transaction(op)

    # ---- leaderboards ---------------------------------------------
    async def leaderboard(self, category: str, page: int, per_page: int = 10) -> tuple[list[dict], int, str]:
        if category not in LEADERBOARDS:
            raise EconomyError("Unknown leaderboard.")
        q, count_q, label = LEADERBOARDS[category]
        total = (await self.db.fetchone(count_q))["n"]
        pages = max(1, -(-total // per_page))
        page = min(max(1, page), pages)
        rows = await self.db.fetchall(q, (per_page, (page - 1) * per_page))
        return rows, pages, label

    async def xp_rank(self, user_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*)+1 AS r FROM users WHERE xp > (SELECT xp FROM users WHERE user_id=?)", (user_id,))
        return row["r"]
