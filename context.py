"""Shared application context passed to every handler module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from utils.helpers import RateLimiter, TTLCache


@dataclass
class Ctx:
    config: Any
    db: Any
    music: Any
    player: Any
    bot: Any = None            # pyrogram Client (bot account)
    assistant: Any = None      # pyrogram Client (user account, for voice chats)
    limiter: RateLimiter = field(default_factory=RateLimiter)
    selections: TTLCache = field(default_factory=lambda: TTLCache(ttl=300, max_size=1000))
    admin_cache: TTLCache = field(default_factory=lambda: TTLCache(ttl=60, max_size=2000))
    listeners: dict[str, list[Callable[..., Awaitable[None]]]] = field(default_factory=dict)
    background: list[Callable[["Ctx"], Awaitable[None]]] = field(default_factory=list)
    np_messages: dict[int, int] = field(default_factory=dict)
    economy: Any = None
    progress: Any = None
    waifus: Any = None
    gacha: Any = None
    anime: Any = None
    images: Any = None
    activity: Any = None
    http: Any = None
    gacha_skip: set = field(default_factory=set)
    known_chats: set = field(default_factory=set)
    bot_username: str = ""

    def on(self, event: str, fn: Callable[..., Awaitable[None]]) -> None:
        self.listeners.setdefault(event, []).append(fn)

    async def emit(self, event: str, **kw: Any) -> None:
        import logging
        for fn in self.listeners.get(event, []):
            try:
                await fn(self, **kw)
            except Exception:
                logging.getLogger(__name__).exception("Listener for %s failed", event)
