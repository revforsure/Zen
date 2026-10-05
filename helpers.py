"""Pure helpers: name matching, TTL cache, rate limiting, URL validation."""
from __future__ import annotations

import ipaddress
import re
import time
import unicodedata
from collections import OrderedDict, defaultdict, deque
from typing import Any, Iterable
from urllib.parse import urlparse

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize_name(text: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCT.sub(" ", text.lower().replace("_", " "))
    return _SPACE.sub(" ", text).strip()


def name_matches(guess: str, name: str, aliases: Iterable[str] = ()) -> bool:
    """Strict-but-friendly character name matching.

    Accepts: exact normalized match, any alias, same words in any order,
    or a single whole word (>=3 chars) of the full name ("mikasa").
    Never accepts substrings or edit-distance fuzzy matches.
    """
    g = normalize_name(guess)
    if len(g) < 3:
        return False
    candidates = [normalize_name(name)] + [normalize_name(a) for a in aliases]
    for cand in candidates:
        if not cand:
            continue
        if g == cand:
            return True
        if sorted(g.split()) == sorted(cand.split()):
            return True
    return " " not in g and g in set(candidates[0].split())


class TTLCache:
    """Bounded in-memory cache with per-entry expiry."""

    def __init__(self, ttl: float = 600, max_size: int = 512):
        self.ttl = ttl
        self.max_size = max_size
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any, default: Any = None) -> Any:
        item = self._data.get(key)
        if item is None:
            return default
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return default
        self._data.move_to_end(key)
        return value

    def set(self, key: Any, value: Any, ttl: float | None = None) -> None:
        self._data[key] = (time.monotonic() + (ttl if ttl is not None else self.ttl), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)

    def pop(self, key: Any, default: Any = None) -> Any:
        value = self.get(key, default)
        self._data.pop(key, None)
        return value

    def __len__(self) -> int:
        return len(self._data)


class RateLimiter:
    """Sliding-window limiter keyed per (scope, key). Not global."""

    def __init__(self) -> None:
        self._hits: dict[tuple, deque] = defaultdict(deque)

    def check(self, scope: str, key: Any, limit: int, window: float) -> float:
        """Return 0 if allowed (and record the hit), else seconds to wait."""
        now = time.monotonic()
        hits = self._hits[(scope, key)]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return max(0.1, hits[0] + window - now)
        hits.append(now)
        return 0.0


def is_safe_url(url: str, max_len: int = 4096) -> bool:
    """Allow only http(s) URLs that do not point at local/private hosts."""
    if not url or len(url) > max_len or any(c in url for c in "\r\n\x00 "):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)


def format_duration(seconds: int) -> str:
    if not seconds or seconds < 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def safe_callback_parts(data: str, expected_prefix: str, min_parts: int = 1, max_len: int = 64) -> list[str] | None:
    """Validate compact callback data 'a:b:c'. Returns parts or None if invalid."""
    if not data or len(data) > max_len or not re.fullmatch(r"[A-Za-z0-9_:\-\.]+", data):
        return None
    parts = data.split(":")
    if parts[0] != expected_prefix or len(parts) < min_parts:
        return None
    return parts


def parse_time(text: str) -> int | None:
    """'90' -> 90, '1:30' -> 90, '1:02:03' -> 3723. None if invalid."""
    text = (text or "").strip()
    if not re.fullmatch(r"\d{1,5}(:\d{1,2}){0,2}", text):
        return None
    total = 0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total
