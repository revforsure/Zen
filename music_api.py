"""Client for https://github.com/mohd-baquir-qureshi/music-api (as documented in its README).

Documented behaviour (the repository ships no source code, only a README):
  GET /search?q=<name>&searchEngine=<gaama|seevn|hunjama|mtmusic|wunk>
      -> {"status":200,"response":[{"id","title","img"}],"message":"success"}
  GET /fetch?id=<id>   -> {"status":200,"response":"<url>"}  (gaama: .m3u8 HLS, others: mp3/mp4)
  GET /lyrics?id=<id>  -> {"status":200,"response":"<html>"}  (gaama only, beta)
The API gives NO artist, album or duration, so those are best-effort/unknown.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from urllib.parse import urlparse

import httpx

from models.music import Track
from services.errors import BotError
from utils.helpers import TTLCache, is_safe_url

log = logging.getLogger(__name__)

KNOWN_ENGINES = ("gaama", "seevn", "hunjama", "mtmusic", "wunk")
MAX_BODY_BYTES = 2_000_000
_FROM_RE = re.compile(r'\(\s*(?:From|from)\s+["“”]?([^"“”)]+)["“”]?\s*\)')
_TAG_RE = re.compile(r"<[^>]+>")


class MusicAPIError(BotError):
    """Any failure talking to / parsing the music API (safe to show to users)."""


def parse_search_response(data: object, engine: str, limit: int = 6) -> list[Track]:
    if not isinstance(data, dict) or data.get("status") != 200:
        raise MusicAPIError("The music service returned an error.")
    items = data.get("response")
    if not isinstance(items, list):
        return []
    tracks: list[Track] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sid, title = item.get("id"), item.get("title")
        if not isinstance(sid, str) or not isinstance(title, str) or not sid or not title.strip():
            continue
        title = html.unescape(title).strip()
        album = ""
        m = _FROM_RE.search(title)
        if m:
            album = m.group(1).strip()
        thumb = item.get("img") if isinstance(item.get("img"), str) else ""
        tracks.append(Track(title=title[:200], album=album[:120], thumbnail=thumb if is_safe_url(thumb) else "",
                            source=engine, source_id=sid))
        if len(tracks) >= limit:
            break
    return tracks


def detect_stream_kind(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith((".mp4", ".m4v", ".webm")):
        return "video"
    return "audio"


def clean_lyrics(raw: str) -> str:
    text = re.sub(r"(?i)</p>|<br\s*/?>", "\n", raw)
    text = html.unescape(_TAG_RE.sub("", text))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class MusicAPI:
    def __init__(self, base_url: str, engines: list[str], cache_ttl: int = 600,
                 client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.engines = [e for e in engines if e in KNOWN_ENGINES] or ["gaama"]
        self._cache = TTLCache(ttl=cache_ttl, max_size=256)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=8.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"User-Agent": "ZenithBot/1.0"}, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict, retries: int = 2) -> object:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = await self._client.get(f"{self.base_url}{path}", params=params)
                if resp.status_code >= 500:
                    raise MusicAPIError(f"HTTP {resp.status_code}")
                if len(resp.content) > MAX_BODY_BYTES:
                    raise MusicAPIError("Response too large.")
                return resp.json()
            except (httpx.HTTPError, ValueError, MusicAPIError) as exc:
                last = exc
                log.warning("Music API %s failed (attempt %d): %s", path, attempt + 1, type(exc).__name__)
                await asyncio.sleep(0.5 * (attempt + 1))
        log.error("Music API request failed: %s", path)
        raise MusicAPIError("The music service is unreachable right now. Try again later.") from last

    async def search(self, query: str, limit: int = 6, engines: list[str] | None = None) -> list[Track]:
        query = " ".join((query or "").split())[:100]
        if not query:
            return []
        log.info("Music search: %s", query)
        errors = 0
        engine_list = [e for e in (engines or self.engines) if e in KNOWN_ENGINES] or self.engines
        for engine in engine_list:
            key = (engine, query.lower())
            cached = self._cache.get(key)
            if cached is not None:
                if cached:
                    return [Track.from_dict(t.to_dict()) for t in cached[:limit]]
                continue
            try:
                data = await self._get_json("/search", {"q": query, "searchEngine": engine})
                tracks = parse_search_response(data, engine, limit)
            except MusicAPIError:
                errors += 1
                continue
            self._cache.set(key, tracks)
            if tracks:
                return tracks
        if errors == len(engine_list):
            raise MusicAPIError("The music service is unreachable right now. Try again later.")
        return []

    async def fetch_stream(self, track: Track) -> Track:
        """Resolve a fresh stream URL (never cached: URLs may expire)."""
        data = await self._get_json("/fetch", {"id": track.source_id})
        if not isinstance(data, dict) or data.get("status") != 200 or not isinstance(data.get("response"), str):
            raise MusicAPIError("Could not get a stream for this track.")
        url = data["response"].strip()
        if not is_safe_url(url):
            raise MusicAPIError("The stream URL was rejected as unsafe.")
        track.stream_url = url
        track.stream_kind = detect_stream_kind(url)
        return track

    async def lyrics(self, track: Track) -> str | None:
        if track.source != "gaama":
            return None
        try:
            data = await self._get_json("/lyrics", {"id": track.source_id}, retries=1)
        except MusicAPIError:
            return None
        if not isinstance(data, dict) or data.get("status") != 200 or not isinstance(data.get("response"), str):
            return None
        text = clean_lyrics(data["response"])
        return text or None

    async def find_lyrics(self, track: Track, loose_query: bool = False) -> tuple[str, str] | None:
        """Return (matched_title, lyrics) or None. Never guesses: for non-gaama tracks a gaama
        result is used only if its core title equals the track's core title."""
        if track.source == "gaama":
            text = await self.lyrics(track)
            return (track.title, text) if text else None
        base = core_title(track.title)
        try:
            candidates = await self.search(base, limit=3, engines=["gaama"])
        except MusicAPIError:
            return None
        for cand in candidates:
            if core_title(cand.title) == base:
                text = await self.lyrics(cand)
                if text:
                    return cand.title, text
        return None


def core_title(title: str) -> str:
    from utils.helpers import normalize_name
    return normalize_name(re.sub(r"\(.*?\)|\[.*?\]", "", title))
