"""Quests, achievements, statistics and the message-activity tracker."""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from services.economy_service import EconomyError, add_currency, add_xp

# quest_id: (label, kind, target, coins, gems, xp)
QUESTS = {
    "songs3": ("🎵 Play 3 songs", "song", 3, 300, 15, 40),
    "guess2": ("🌸 Guess 2 waifus", "guess", 2, 400, 20, 50),
    "pulls5": ("🎴 Perform 5 pulls", "pull", 5, 300, 25, 40),
    "msgs20": ("💬 Send 20 messages", "message", 20, 200, 10, 30),
}

# id: (name, description, predicate(stats), gem reward)
ACHIEVEMENTS = {
    "first_guess": ("🎯 First Guess", "Guess a waifu correctly", lambda s: s["guesses"] >= 1, 20),
    "music_lover": ("🎵 Music Lover", "Have 10 songs played", lambda s: s["songs"] >= 10, 20),
    "songs100": ("🎧 100 Songs", "Have 100 songs played", lambda s: s["songs"] >= 100, 100),
    "first_waifu": ("🌸 First Waifu", "Own your first waifu", lambda s: s["waifus"] >= 1, 20),
    "waifu_collector": ("💮 Waifu Collector", "Own 25 waifus", lambda s: s["waifus"] >= 25, 60),
    "lucky_pull": ("🎴 Lucky Pull", "Pull an SSR or UR", lambda s: s["top_pulls"] >= 1, 50),
    "ssr_hunter": ("🔥 SSR Hunter", "Pull 5 SSR/UR characters", lambda s: s["top_pulls"] >= 5, 150),
    "collector": ("👑 Collector", "Own 100 waifus", lambda s: s["waifus"] >= 100, 300),
    "millionaire": ("💎 Millionaire", "Earn 1,000,000 coins in total", lambda s: s["coins_earned"] >= 1_000_000, 200),
    "veteran": ("🏆 Veteran", "Reach level 20", lambda s: s["level"] >= 20, 200),
}


def today_key(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), timezone.utc).strftime("%Y-%m-%d")


class Activity:
    """In-memory message counters (cheap) + per-user XP cooldown."""

    def __init__(self, xp_cooldown: float = 30):
        self.xp_cooldown = xp_cooldown
        self.chat_last: dict[int, float] = {}
        self.chat_msgs: dict[int, int] = defaultdict(int)
        self.user_msgs: dict[int, int] = defaultdict(int)
        self.user_last_xp: dict[int, float] = {}

    def note(self, chat_id: int, user_id: int, now: float | None = None) -> bool:
        """Record a message. Returns True if this message may award XP."""
        now = now if now is not None else time.monotonic()
        self.chat_last[chat_id] = now
        self.chat_msgs[chat_id] += 1
        self.user_msgs[user_id] += 1
        if now - self.user_last_xp.get(user_id, -1e9) >= self.xp_cooldown:
            self.user_last_xp[user_id] = now
            return True
        return False

    def is_active(self, chat_id: int, within: float, min_msgs: int, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return now - self.chat_last.get(chat_id, -1e9) <= within and self.chat_msgs.get(chat_id, 0) >= min_msgs

    def reset_chat(self, chat_id: int) -> None:
        self.chat_msgs[chat_id] = 0

    def drain_users(self) -> dict[int, int]:
        out, self.user_msgs = dict(self.user_msgs), defaultdict(int)
        cutoff = time.monotonic() - 3600
        self.user_last_xp = {u: t for u, t in self.user_last_xp.items() if t > cutoff}
        return out


class Progress:
    def __init__(self, db):
        self.db = db

    # ---- stats ----------------------------------------------------
    async def stats(self, user_id: int) -> dict:
        await self.db.ensure_user(user_id)
        row = await self.db.fetchone(
            "SELECT u.xp, u.level, u.songs_played AS songs, u.messages_count AS messages, u.favorite_waifu_id, "
            "e.coins, e.gems, e.total_coins_earned AS coins_earned, e.total_gems_earned AS gems_earned, "
            "e.gacha_pulls AS pulls, d.streak, "
            "(SELECT COUNT(*) FROM user_waifus WHERE user_id=u.user_id) AS waifus, "
            "(SELECT COUNT(*) FROM achievements WHERE user_id=u.user_id) AS achievements, "
            "(SELECT COUNT(*) FROM waifu_guesses WHERE user_id=u.user_id AND correct=1) AS guesses, "
            "(SELECT COUNT(*) FROM gacha_history WHERE user_id=u.user_id AND rarity IN ('SSR','UR')) AS top_pulls "
            "FROM users u JOIN economy e ON e.user_id=u.user_id JOIN daily_rewards d ON d.user_id=u.user_id "
            "WHERE u.user_id=?", (user_id,))
        return row

    # ---- achievements ---------------------------------------------
    async def check_achievements(self, user_id: int) -> list[str]:
        """Unlock anything newly earned; returns display names of new unlocks."""
        stats = await self.stats(user_id)
        have = {r["achievement_id"] for r in await self.db.fetchall(
            "SELECT achievement_id FROM achievements WHERE user_id=?", (user_id,))}
        new = []
        for aid, (name, _, pred, gems) in ACHIEVEMENTS.items():
            if aid in have or not pred(stats):
                continue

            def op(c, aid=aid, gems=gems):
                cur = c.execute("INSERT OR IGNORE INTO achievements (user_id, achievement_id) VALUES (?,?)",
                                (user_id, aid))
                if cur.rowcount == 1:
                    add_currency(c, user_id, gems=gems)
                    return True
                return False
            if await self.db.transaction(op):
                new.append(f"{name} (+{gems} 💎)")
        return new

    async def unlocked(self, user_id: int) -> set[str]:
        return {r["achievement_id"] for r in await self.db.fetchall(
            "SELECT achievement_id FROM achievements WHERE user_id=?", (user_id,))}

    # ---- quests ---------------------------------------------------
    async def quests(self, user_id: int) -> list[dict]:
        await self.db.ensure_user(user_id)
        day = today_key()

        def op(c):
            for qid in QUESTS:
                c.execute("INSERT OR IGNORE INTO quests (user_id, quest_id, day) VALUES (?,?,?)", (user_id, qid, day))
            return [dict(r) for r in c.execute(
                "SELECT quest_id, progress, claimed FROM quests WHERE user_id=? AND day=?", (user_id, day))]
        rows = await self.db.transaction(op)
        out = []
        for r in rows:
            label, kind, target, coins, gems, xp = QUESTS[r["quest_id"]]
            out.append({"id": r["quest_id"], "label": label, "progress": min(r["progress"], target),
                        "target": target, "claimed": bool(r["claimed"]), "coins": coins, "gems": gems, "xp": xp})
        return sorted(out, key=lambda q: list(QUESTS).index(q["id"]))

    async def add_progress(self, user_id: int, kind: str, n: int = 1) -> None:
        day = today_key()
        ids = [(qid, q[2]) for qid, q in QUESTS.items() if q[1] == kind]

        def op(c):
            for qid, target in ids:
                c.execute("INSERT OR IGNORE INTO quests (user_id, quest_id, day) VALUES (?,?,?)", (user_id, qid, day))
                c.execute("UPDATE quests SET progress=MIN(progress+?, ?) WHERE user_id=? AND quest_id=? AND day=? "
                          "AND claimed=0", (n, target, user_id, qid, day))
        await self.db.ensure_user(user_id)
        await self.db.transaction(op)

    async def claim_quest(self, user_id: int, quest_id: str) -> dict:
        if quest_id not in QUESTS:
            raise EconomyError("Unknown quest.")
        label, _, target, coins, gems, xp = QUESTS[quest_id]
        day = today_key()

        def op(c):
            row = c.execute("SELECT progress, claimed FROM quests WHERE user_id=? AND quest_id=? AND day=?",
                            (user_id, quest_id, day)).fetchone()
            if row is None or row["progress"] < target:
                raise EconomyError("That quest isn't finished yet.")
            cur = c.execute("UPDATE quests SET claimed=1 WHERE user_id=? AND quest_id=? AND day=? AND claimed=0",
                            (user_id, quest_id, day))
            if cur.rowcount != 1:
                raise EconomyError("Already claimed.")
            add_currency(c, user_id, coins, gems)
            add_xp(c, user_id, xp)
            return {"label": label, "coins": coins, "gems": gems, "xp": xp}
        await self.db.ensure_user(user_id)
        return await self.db.transaction(op)

    async def claim_all(self, user_id: int) -> list[dict]:
        out = []
        for q in await self.quests(user_id):
            if not q["claimed"] and q["progress"] >= q["target"]:
                try:
                    out.append(await self.claim_quest(user_id, q["id"]))
                except EconomyError:
                    pass
        return out

    # ---- activity flush -------------------------------------------
    async def flush_activity(self, activity: Activity) -> None:
        for uid, n in activity.drain_users().items():
            await self.db.ensure_user(uid)
            await self.db.execute("UPDATE users SET messages_count=messages_count+? WHERE user_id=?", (n, uid))
            await self.add_progress(uid, "message", n)
