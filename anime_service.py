"""Character data from AniList's public GraphQL API (https://graphql.anilist.co).

One request returns character + aliases + artwork + anime title, so syncing ~600 characters
takes ~12 polite requests (AniList allows 90/min; we wait >=1.2s between calls). Pages are cached
in SQLite for 7 days; if the API is down the existing database pool keeps working.
New sources can be added by writing another `sync_*` function that upserts into `waifus`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from config import RARITY_ORDER, RARITY_VALUES

log = logging.getLogger(__name__)
ANILIST_URL = "https://graphql.anilist.co"
CACHE_TTL = 7 * 86400
PER_PAGE = 50

CHAR_QUERY = """
query ($page: Int) {
  Page(page: $page, perPage: %d) {
    pageInfo { hasNextPage }
    characters(sort: FAVOURITES_DESC) {
      id gender favourites
      name { full native alternative }
      image { large }
      media(perPage: 1, sort: POPULARITY_DESC) { nodes { title { romaji english } startDate { year } genres } }
    }
  }
}""" % PER_PAGE

SEARCH_CHAR = """
query ($search: String) {
  Character(search: $search) {
    id name { full native alternative } image { large } favourites description(asHtml: false)
    media(perPage: 3, sort: POPULARITY_DESC) { nodes { title { romaji english } } }
  }
}"""
SEARCH_ANIME = """
query ($search: String, $id: Int) {
  Media(search: $search, id: $id, type: ANIME, sort: POPULARITY_DESC) {
    id title { romaji english } description(asHtml: false) episodes status averageScore
    genres season seasonYear coverImage { large }
  }
}"""
RANDOM_ANIME = """
query ($page: Int) {
  Page(page: $page, perPage: 1) { media(type: ANIME, sort: POPULARITY_DESC) {
    id title { romaji english } description(asHtml: false) episodes averageScore genres seasonYear coverImage { large } } }
}"""


def parse_characters(data: object) -> list[dict]:
    """Pure parser: AniList page JSON -> list of female characters with usable artwork (order kept)."""
    try:
        chars = data["data"]["Page"]["characters"]  # type: ignore[index]
    except (KeyError, TypeError):
        return []
    out = []
    for ch in chars or []:
        if not isinstance(ch, dict) or (ch.get("gender") or "").lower() != "female":
            continue
        img = (ch.get("image") or {}).get("large") or ""
        nodes = ((ch.get("media") or {}).get("nodes")) or []
        name = ((ch.get("name") or {}).get("full") or "").strip()
        if not name or not img.startswith("https://") or "default" in img or not nodes:
            continue
        title = (nodes[0].get("title") or {})
        anime = (title.get("english") or title.get("romaji") or "").strip()
        if not anime:
            continue
        nm = ch["name"]
        aliases = [a.strip() for a in (nm.get("alternative") or []) if isinstance(a, str) and a.strip()]
        if nm.get("native"):
            aliases.append(nm["native"])
        genres = nodes[0].get("genres") or []
        year = (nodes[0].get("startDate") or {}).get("year")
        out.append({"source_id": str(ch["id"]), "name": name, "anime": anime, "image_url": img,
                    "aliases": aliases[:12], "favourites": ch.get("favourites") or 0,
                    "tags": "battle" if "Action" in genres else "", "release_info": str(year or "")})
    return out


def assign_rarities(chars: list[dict], weights: dict[str, float]) -> list[dict]:
    """Rank-based: most-favourited characters get the rarest tiers, proportionally to `weights`."""
    chars = sorted(chars, key=lambda c: -c["favourites"])
    total = len(chars)
    wsum = sum(weights.get(r, 0) for r in RARITY_ORDER) or 1
    idx = 0
    for rarity in reversed(RARITY_ORDER):
        w = weights.get(rarity, 0)
        if w <= 0:
            continue
        n = max(1, round(total * w / wsum))
        for ch in chars[idx: idx + n]:
            ch["rarity"] = rarity
        idx += n
    for ch in chars:
        ch.setdefault("rarity", "COMMON")
    return chars


class AnimeService:
    def __init__(self, db, http: httpx.AsyncClient, weights: dict[str, float], pages: int = 12):
        self.db, self.http, self.weights, self.pages = db, http, weights, pages
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def _graphql(self, query: str, variables: dict) -> dict | None:
        wait = 1.2 - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()
        try:
            resp = await self.http.post(ANILIST_URL, json={"query": query, "variables": variables}, timeout=20)
            if resp.status_code == 429:
                log.warning("AniList rate limit hit; backing off")
                await asyncio.sleep(int(resp.headers.get("Retry-After", "30")))
                return None
            if resp.status_code != 200 or len(resp.content) > 3_000_000:
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("AniList request failed: %s", type(exc).__name__)
            return None

    async def _cached(self, key: str) -> str | None:
        row = await self.db.fetchone("SELECT value FROM api_cache WHERE key=? AND expires_at>?", (key, time.time()))
        return row["value"] if row else None

    async def _stale(self, key: str) -> str | None:
        row = await self.db.fetchone("SELECT value FROM api_cache WHERE key=?", (key,))
        return row["value"] if row else None

    async def _store(self, key: str, value: str, ttl: float) -> None:
        await self.db.execute(
            "INSERT INTO api_cache (key,value,expires_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
            (key, value, time.time() + ttl))

    async def pool_size(self) -> int:
        return (await self.db.fetchone("SELECT COUNT(*) AS n FROM waifus WHERE enabled=1"))["n"]

    async def needs_sync(self) -> bool:
        return await self._cached("anilist:synced") is None or await self.pool_size() < 30

    async def sync_characters(self) -> int:
        """Fetch (or reuse cached) pages, assign rarities, upsert into `waifus`. Returns pool size."""
        async with self._lock:
            collected: list[dict] = []
            for page in range(1, self.pages + 1):
                key = f"anilist:chars:{page}"
                raw = await self._cached(key)
                if raw is None:
                    data = await self._graphql(CHAR_QUERY, {"page": page})
                    parsed = parse_characters(data) if data else []
                    if parsed:
                        await self._store(key, json.dumps(parsed), CACHE_TTL)
                    else:
                        raw = await self._stale(key)
                        parsed = json.loads(raw) if raw else []
                else:
                    parsed = json.loads(raw)
                collected += parsed
            if not collected:
                log.warning("Anime sync produced no characters; keeping the existing pool")
                return await self.pool_size()
            rows = assign_rarities(collected, self.weights)

            def op(c):
                for ch in rows:
                    c.execute(
                        "INSERT INTO waifus (name,anime,image_url,rarity,aliases,value,source,source_id,release_info,tags) "
                        "VALUES (?,?,?,?,?,?, 'anilist', ?,?,?) "
                        "ON CONFLICT(source,source_id) DO UPDATE SET name=excluded.name, anime=excluded.anime, "
                        "image_url=excluded.image_url, rarity=excluded.rarity, aliases=excluded.aliases, "
                        "value=excluded.value, tags=excluded.tags, release_info=excluded.release_info",
                        (ch["name"], ch["anime"], ch["image_url"], ch["rarity"], json.dumps(ch["aliases"]),
                         RARITY_VALUES[ch["rarity"]], ch["source_id"], ch["release_info"], ch["tags"]))
            await self.db.transaction(op)
            await self._store("anilist:synced", "1", CACHE_TTL)
            log.info("Anime pool synced: %d characters", len(rows))
            return await self.pool_size()

    # ---- lookups for /anime /character /randomanime (cached) ---------
    async def _lookup(self, key: str, query: str, variables: dict, ttl: float = 86400) -> dict | None:
        raw = await self._cached(key)
        if raw:
            return json.loads(raw)
        data = await self._graphql(query, variables)
        if data and isinstance(data.get("data"), dict):
            await self._store(key, json.dumps(data["data"]), ttl)
            return data["data"]
        raw = await self._stale(key)
        return json.loads(raw) if raw else None

    async def search_anime(self, name: str) -> dict | None:
        d = await self._lookup(f"anime:{name.lower()[:60]}", SEARCH_ANIME, {"search": name[:60]})
        return (d or {}).get("Media")

    async def search_character(self, name: str) -> dict | None:
        d = await self._lookup(f"char:{name.lower()[:60]}", SEARCH_CHAR, {"search": name[:60]})
        return (d or {}).get("Character")

    async def random_anime(self, page: int) -> dict | None:
        d = await self._lookup(f"randanime:{page}", RANDOM_ANIME, {"page": page}, ttl=7 * 86400)
        media = ((d or {}).get("Page") or {}).get("media") or []
        return media[0] if media else None
