"""Waifu spawning, atomic claiming, collections, selling, gifting and trading."""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from config import RARITY_ORDER, RARITY_STARS, RARITY_VALUES
from models.waifu import Waifu
from services.economy_service import EconomyError, add_currency, add_xp
from utils.helpers import name_matches, normalize_name

TRADE_TTL = 300


class _SwapFailed(EconomyError):
    """A character changed hands mid-trade; the whole transaction rolls back."""


def pick_rarity(weights: dict[str, float], rng: random.Random, available: set[str] | None = None,
                multipliers: dict[str, float] | None = None, min_rarity: str | None = None) -> str:
    """Weighted rarity choice. `available` restricts to rarities that have characters;
    `min_rarity` supports pity; `multipliers` supports banners."""
    floor = RARITY_ORDER.index(min_rarity) if min_rarity else 0
    options, w = [], []
    for i, r in enumerate(RARITY_ORDER):
        if i < floor or (available is not None and r not in available):
            continue
        weight = weights.get(r, 0) * (multipliers or {}).get(r, 1.0)
        if weight > 0:
            options.append(r)
            w.append(weight)
    if not options:
        raise EconomyError("The character pool is empty.")
    return rng.choices(options, weights=w, k=1)[0]


def reward_for(rarity: str) -> dict:
    stars = RARITY_ORDER.index(rarity) + 1  # 1..8
    return {"coins": 50 + RARITY_VALUES[rarity] // 20, "gems": 3 * stars, "xp": 20 + 5 * stars}


@dataclass
class GuessResult:
    status: str  # no_spawn | wrong | correct | lost
    waifu: Waifu | None = None
    rewards: dict | None = None
    uwid: int = 0


class WaifuService:
    def __init__(self, db, weights: dict[str, float], rng: random.Random | None = None):
        self.db, self.weights = db, weights
        self.rng = rng or random.Random()
        self._recent: dict[int, list[int]] = {}

    # ---- catalogue ------------------------------------------------
    async def get(self, waifu_id: int) -> Waifu | None:
        row = await self.db.fetchone("SELECT * FROM waifus WHERE id=?", (waifu_id,))
        return Waifu.from_row(row) if row else None

    async def available_rarities(self, tag: str = "") -> set[str]:
        if tag:
            rows = await self.db.fetchall("SELECT DISTINCT rarity FROM waifus WHERE enabled=1 AND "
                                          "instr(','||tags||',', ','||?||',')>0", (tag,))
            if rows:
                return {r["rarity"] for r in rows}
        return {r["rarity"] for r in await self.db.fetchall("SELECT DISTINCT rarity FROM waifus WHERE enabled=1")}

    async def random_of_rarity(self, rarity: str, tag: str = "", exclude: list[int] | None = None) -> Waifu | None:
        exclude = exclude or []
        for use_tag in ([tag, ""] if tag else [""]):
            sql = "SELECT * FROM waifus WHERE enabled=1 AND rarity=?"
            params: list = [rarity]
            if use_tag:
                sql += " AND instr(','||tags||',', ','||?||',')>0"
                params.append(use_tag)
            rows = await self.db.fetchall(sql, params)
            fresh = [r for r in rows if r["id"] not in exclude] or rows
            if fresh:
                return Waifu.from_row(self.rng.choice(fresh))
        return None

    async def random_waifu(self) -> Waifu | None:
        rows = await self.db.fetchall("SELECT * FROM waifus WHERE enabled=1 ORDER BY RANDOM() LIMIT 1")
        return Waifu.from_row(rows[0]) if rows else None

    async def disable(self, waifu_id: int) -> None:
        await self.db.execute("UPDATE waifus SET enabled=0 WHERE id=?", (waifu_id,))

    async def search_catalogue(self, query: str, limit: int = 10) -> list[Waifu]:
        q = normalize_name(query)
        if len(q) < 2:
            return []
        out = []
        for row in await self.db.fetchall("SELECT * FROM waifus WHERE enabled=1"):
            w = Waifu.from_row(row)
            if q in normalize_name(w.name) or any(q == normalize_name(a) for a in w.aliases):
                out.append(w)
        out.sort(key=lambda w: (normalize_name(w.name) != q, -RARITY_ORDER.index(w.rarity)))
        return out[:limit]

    # ---- spawns ---------------------------------------------------
    async def pick_spawn(self, chat_id: int) -> Waifu | None:
        avail = await self.available_rarities()
        if not avail:
            return None
        rarity = pick_rarity(self.weights, self.rng, avail)
        recent = self._recent.setdefault(chat_id, [])
        w = await self.random_of_rarity(rarity, exclude=recent)
        if w:
            recent.append(w.id)
            del recent[:-30]
        return w

    async def active_spawn(self, chat_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM active_spawns WHERE chat_id=? AND claimed_by IS NULL "
                                      "ORDER BY id DESC LIMIT 1", (chat_id,))

    async def create_spawn(self, chat_id: int, waifu_id: int, message_id: int | None) -> int:
        now = time.time()

        def op(c):
            cur = c.execute("INSERT INTO active_spawns (chat_id, waifu_id, message_id, spawned_at) VALUES (?,?,?,?)",
                            (chat_id, waifu_id, message_id, now))
            c.execute("UPDATE groups SET last_spawn_at=? WHERE chat_id=?", (now, chat_id))
            return cur.lastrowid
        return await self.db.transaction(op)

    async def mark_spawn_attempt(self, chat_id: int) -> None:
        await self.db.execute("UPDATE groups SET last_spawn_at=? WHERE chat_id=?", (time.time(), chat_id))

    async def expire_old(self, max_age: float) -> int:
        """Unclaimed spawns older than max_age are retired (claimed_by=0)."""
        return await self.db.execute("UPDATE active_spawns SET claimed_by=0, claimed_at=? "
                                     "WHERE claimed_by IS NULL AND spawned_at<?", (time.time(), time.time() - max_age))

    # ---- guessing -------------------------------------------------
    async def guess(self, chat_id: int, user_id: int, text: str) -> GuessResult:
        spawn = await self.active_spawn(chat_id)
        if not spawn:
            return GuessResult("no_spawn")
        waifu = await self.get(spawn["waifu_id"])
        if not waifu:
            return GuessResult("no_spawn")
        correct = name_matches(text, waifu.name, waifu.aliases)
        await self.db.execute("INSERT INTO waifu_guesses (chat_id,user_id,spawn_id,guess,correct) VALUES (?,?,?,?,0)",
                              (chat_id, user_id, spawn["id"], normalize_name(text)[:60]))
        if not correct:
            return GuessResult("wrong")
        rewards = reward_for(waifu.rarity)
        await self.db.ensure_user(user_id)

        def op(c):
            cur = c.execute("UPDATE active_spawns SET claimed_by=?, claimed_at=? WHERE id=? AND claimed_by IS NULL",
                            (user_id, time.time(), spawn["id"]))
            if cur.rowcount != 1:
                return 0  # someone else was first
            uw = c.execute("INSERT INTO user_waifus (user_id, waifu_id) VALUES (?,?)",
                           (user_id, waifu.id)).lastrowid
            c.execute("UPDATE waifu_guesses SET correct=1 WHERE id=(SELECT MAX(id) FROM waifu_guesses "
                      "WHERE spawn_id=? AND user_id=?)", (spawn["id"], user_id))
            add_currency(c, user_id, rewards["coins"], rewards["gems"])
            add_xp(c, user_id, rewards["xp"])
            return uw
        uwid = await self.db.transaction(op)
        if not uwid:
            return GuessResult("lost", waifu)
        return GuessResult("correct", waifu, rewards, uwid)

    # ---- collection -----------------------------------------------
    async def collection(self, user_id: int, page: int, per_page: int = 5) -> tuple[list[dict], int, int]:
        total = (await self.db.fetchone("SELECT COUNT(*) AS n FROM user_waifus WHERE user_id=?", (user_id,)))["n"]
        pages = max(1, -(-total // per_page))
        page = min(max(1, page), pages)
        order = "CASE w.rarity " + " ".join(f"WHEN '{r}' THEN {i}" for i, r in enumerate(RARITY_ORDER)) + " END DESC"
        rows = await self.db.fetchall(
            "SELECT uw.id AS uwid, uw.favorite, w.* FROM user_waifus uw JOIN waifus w ON w.id=uw.waifu_id "
            f"WHERE uw.user_id=? ORDER BY uw.favorite DESC, {order}, w.name LIMIT ? OFFSET ?",
            (user_id, per_page, (page - 1) * per_page))
        return rows, page, pages

    async def owned(self, user_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT uw.id AS uwid, uw.favorite, w.* FROM user_waifus uw JOIN waifus w ON w.id=uw.waifu_id "
            "WHERE uw.user_id=?", (user_id,))

    async def find_owned(self, user_id: int, query: str, include_favorites: bool = True) -> dict | None:
        """Look up one of the user's own characters by (partial) name. Lookup only — not a guess."""
        q = normalize_name(query)
        if len(q) < 2:
            return None
        best = None
        for row in await self.owned(user_id):
            if not include_favorites and row["favorite"]:
                continue
            w = Waifu.from_row(row)
            names = [normalize_name(w.name)] + [normalize_name(a) for a in w.aliases]
            exact = q in names
            if exact or q in names[0]:
                score = (exact, RARITY_ORDER.index(w.rarity), -row["uwid"])
                if best is None or score > best[0]:
                    best = (score, row)
        return best[1] if best else None

    async def first_owner(self, waifu_id: int) -> dict | None:
        return await self.db.fetchone(
            "SELECT u.user_id, u.username, u.first_name FROM user_waifus uw JOIN users u ON u.user_id=uw.user_id "
            "WHERE uw.waifu_id=? ORDER BY uw.id LIMIT 1", (waifu_id,))

    async def set_favorite(self, user_id: int, uwid: int, value: bool) -> bool:
        return (await self.db.execute("UPDATE user_waifus SET favorite=? WHERE id=? AND user_id=?",
                                      (int(value), uwid, user_id))) == 1

    async def toggle_favorite(self, user_id: int, uwid: int) -> bool | None:
        def op(c):
            row = c.execute("SELECT favorite FROM user_waifus WHERE id=? AND user_id=?", (uwid, user_id)).fetchone()
            if not row:
                return None
            new = 0 if row["favorite"] else 1
            c.execute("UPDATE user_waifus SET favorite=? WHERE id=?", (new, uwid))
            return bool(new)
        return await self.db.transaction(op)

    async def set_profile_waifu(self, user_id: int, waifu_id: int) -> None:
        await self.db.execute("UPDATE users SET favorite_waifu_id=? WHERE user_id=?", (waifu_id, user_id))

    async def favorite_waifu(self, user_id: int) -> Waifu | None:
        row = await self.db.fetchone("SELECT w.* FROM users u JOIN waifus w ON w.id=u.favorite_waifu_id "
                                     "JOIN user_waifus uw ON uw.waifu_id=w.id AND uw.user_id=u.user_id "
                                     "WHERE u.user_id=? LIMIT 1", (user_id,))
        return Waifu.from_row(row) if row else None

    # ---- sell / give ----------------------------------------------
    async def sell(self, user_id: int, uwid: int) -> int:
        def op(c):
            row = c.execute("SELECT uw.favorite, w.value FROM user_waifus uw JOIN waifus w ON w.id=uw.waifu_id "
                            "WHERE uw.id=? AND uw.user_id=?", (uwid, user_id)).fetchone()
            if not row:
                raise EconomyError("You don't own that character.")
            if row["favorite"]:
                raise EconomyError("Unfavorite it first — favorites can't be sold.")
            if c.execute("SELECT 1 FROM trades WHERE status='pending' AND (give_instance=? OR want_instance=?)",
                         (uwid, uwid)).fetchone():
                raise EconomyError("That character is part of a pending trade.")
            if c.execute("DELETE FROM user_waifus WHERE id=? AND user_id=? AND favorite=0",
                         (uwid, user_id)).rowcount != 1:
                raise EconomyError("Could not sell that character.")
            price = max(1, row["value"] // 2)
            add_currency(c, user_id, coins=price)
            return price
        return await self.db.transaction(op)

    async def give(self, uwid: int, from_id: int, to_id: int) -> None:
        if from_id == to_id:
            raise EconomyError("You can't give a character to yourself.")
        await self.db.ensure_user(to_id)

        def op(c):
            if c.execute("SELECT 1 FROM trades WHERE status='pending' AND (give_instance=? OR want_instance=?)",
                         (uwid, uwid)).fetchone():
                raise EconomyError("That character is part of a pending trade.")
            cur = c.execute("UPDATE user_waifus SET user_id=?, favorite=0 WHERE id=? AND user_id=? AND favorite=0",
                            (to_id, uwid, from_id))
            if cur.rowcount != 1:
                raise EconomyError("You can't give that (not owned, or it's a favorite).")
            c.execute("UPDATE users SET favorite_waifu_id=NULL WHERE user_id=? AND favorite_waifu_id=("
                      "SELECT waifu_id FROM user_waifus WHERE id=?) AND NOT EXISTS ("
                      "SELECT 1 FROM user_waifus WHERE user_id=? AND waifu_id=(SELECT waifu_id FROM user_waifus WHERE id=?))",
                      (from_id, uwid, from_id, uwid))
        await self.db.transaction(op)

    # ---- trading --------------------------------------------------
    async def create_trade(self, from_id: int, to_id: int, give_uwid: int, want_uwid: int) -> int:
        if from_id == to_id:
            raise EconomyError("You can't trade with yourself.")
        await self.db.ensure_user(to_id)

        def op(c):
            mine = c.execute("SELECT favorite FROM user_waifus WHERE id=? AND user_id=?", (give_uwid, from_id)).fetchone()
            theirs = c.execute("SELECT favorite FROM user_waifus WHERE id=? AND user_id=?", (want_uwid, to_id)).fetchone()
            if not mine or not theirs:
                raise EconomyError("One of those characters is no longer available.")
            if mine["favorite"]:
                raise EconomyError("Unfavorite your character first.")
            if theirs["favorite"]:
                raise EconomyError("That character is protected by its owner.")
            now = time.time()
            c.execute("UPDATE trades SET status='expired' WHERE status='pending' AND created_at<?", (now - TRADE_TTL,))
            busy = c.execute("SELECT 1 FROM trades WHERE status='pending' AND (give_instance IN (?,?) OR "
                             "want_instance IN (?,?))", (give_uwid, want_uwid, give_uwid, want_uwid)).fetchone()
            if busy:
                raise EconomyError("One of those characters is already in a pending trade.")
            return c.execute("INSERT INTO trades (from_user,to_user,give_instance,want_instance,created_at) "
                             "VALUES (?,?,?,?,?)", (from_id, to_id, give_uwid, want_uwid, now)).lastrowid
        return await self.db.transaction(op)

    async def get_trade(self, trade_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM trades WHERE id=?", (trade_id,))

    async def resolve_trade(self, trade_id: int, actor_id: int, accept: bool) -> str:
        """Returns 'accepted' | 'declined' | 'cancelled'. Raises EconomyError on invalid/stale requests."""
        def op(c):
            t = c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            if not t or t["status"] != "pending":
                raise EconomyError("This trade is no longer active.")
            if time.time() - t["created_at"] > TRADE_TTL:
                c.execute("UPDATE trades SET status='expired' WHERE id=?", (trade_id,))
                return "expired"
            if actor_id not in (t["from_user"], t["to_user"]):
                raise EconomyError("This trade isn't yours.")
            if not accept:
                status = "cancelled" if actor_id == t["from_user"] else "declined"
                c.execute("UPDATE trades SET status=? WHERE id=? AND status='pending'", (status, trade_id))
                return status
            if actor_id != t["to_user"]:
                raise EconomyError("Only the person who received the request can accept.")
            if c.execute("UPDATE trades SET status='accepted' WHERE id=? AND status='pending'", (trade_id,)).rowcount != 1:
                raise EconomyError("This trade is no longer active.")
            a = c.execute("UPDATE user_waifus SET user_id=?, favorite=0 WHERE id=? AND user_id=? AND favorite=0",
                          (t["to_user"], t["give_instance"], t["from_user"])).rowcount
            b = c.execute("UPDATE user_waifus SET user_id=?, favorite=0 WHERE id=? AND user_id=? AND favorite=0",
                          (t["from_user"], t["want_instance"], t["to_user"])).rowcount
            if a != 1 or b != 1:
                raise _SwapFailed("The trade failed because a character changed hands.")  # rolls back
            return "accepted"
        try:
            return await self.db.transaction(op)
        except _SwapFailed:
            await self.db.execute("UPDATE trades SET status='failed' WHERE id=? AND status='pending'", (trade_id,))
            raise
