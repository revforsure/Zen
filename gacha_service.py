"""Gacha pulls with banners, pity and virtual-currency-only costs."""
from __future__ import annotations

import json
import random

from config import RARITY_ORDER
from models.waifu import Waifu
from services.economy_service import EconomyError, add_xp
from services.waifu_service import WaifuService, pick_rarity


class GachaService:
    def __init__(self, db, waifus: WaifuService, weights: dict[str, float], pity_threshold: int = 50,
                 ten_discount: float = 0.9, rng: random.Random | None = None):
        self.db, self.waifus, self.weights = db, waifus, weights
        self.pity_threshold, self.ten_discount = pity_threshold, ten_discount
        self.rng = rng or random.Random()

    async def banners(self) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM banners WHERE active=1 ORDER BY rowid")

    async def banner(self, banner_id: str) -> dict | None:
        return await self.db.fetchone("SELECT * FROM banners WHERE id=? AND active=1", (banner_id,))

    def cost(self, banner: dict, count: int) -> int:
        total = banner["cost_gems"] * count
        return int(total * self.ten_discount) if count >= 10 else total

    async def pull(self, user_id: int, banner_id: str, count: int = 1) -> dict:
        if count not in (1, 10):
            raise EconomyError("You can pull 1 or 10 times.")
        banner = await self.banner(banner_id)
        if not banner:
            raise EconomyError("That banner isn't available.")
        await self.db.ensure_user(user_id)
        try:
            mult = {k: float(v) for k, v in json.loads(banner["rate_multipliers"] or "{}").items()}
        except (ValueError, TypeError):
            mult = {}
        tag = banner["pool_tag"]
        avail = await self.waifus.available_rarities(tag)
        if not avail:
            raise EconomyError("The character pool is empty. Ask an admin to sync/add characters.")
        # Pre-roll outside the transaction (needs async catalogue lookups); pity is applied inside.
        cost = self.cost(banner, count)
        legendary_idx = RARITY_ORDER.index("LEGENDARY")

        pool_cache: dict[str, list[dict]] = {}
        for r in avail:
            sql = "SELECT * FROM waifus WHERE enabled=1 AND rarity=?"
            rows = await self.db.fetchall(sql + (" AND instr(','||tags||',', ','||?||',')>0" if tag else ""),
                                          (r, tag) if tag else (r,))
            if not rows and tag:
                rows = await self.db.fetchall(sql, (r,))
            pool_cache[r] = rows

        def op(c):
            if c.execute("UPDATE economy SET gems=gems-? WHERE user_id=? AND gems>=?",
                         (cost, user_id, cost)).rowcount != 1:
                raise EconomyError(f"Not enough gems — you need {cost} 💎.")
            pity = c.execute("SELECT pity FROM economy WHERE user_id=?", (user_id,)).fetchone()["pity"]
            results = []
            for _ in range(count):
                floor = "LEGENDARY" if pity + 1 >= self.pity_threshold else None
                usable = {r for r in avail if pool_cache.get(r)}
                if floor and not any(RARITY_ORDER.index(r) >= legendary_idx for r in usable):
                    floor = None
                rarity = pick_rarity(self.weights, self.rng, usable, mult, floor)
                row = self.rng.choice(pool_cache[rarity])
                pity = 0 if RARITY_ORDER.index(rarity) >= legendary_idx else pity + 1
                uw = c.execute("INSERT INTO user_waifus (user_id, waifu_id) VALUES (?,?)", (user_id, row["id"])).lastrowid
                c.execute("INSERT INTO gacha_history (user_id, banner_id, waifu_id, rarity) VALUES (?,?,?,?)",
                          (user_id, banner_id, row["id"], rarity))
                results.append({"uwid": uw, "waifu": Waifu.from_row(row)})
            c.execute("UPDATE economy SET pity=?, gacha_pulls=gacha_pulls+? WHERE user_id=?", (pity, count, user_id))
            old, new = add_xp(c, user_id, 10 * count)
            return {"results": results, "cost": cost, "level_up": new if new > old else 0, "pity": pity}
        out = await self.db.transaction(op)
        out["banner"] = banner
        out["best"] = max(out["results"], key=lambda r: RARITY_ORDER.index(r["waifu"].rarity))
        return out
