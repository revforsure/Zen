"""Central configuration. Secrets come only from the environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

RARITY_ORDER = ["COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC", "SSR", "UR"]

DEFAULT_RARITY_WEIGHTS = {
    "COMMON": 40.0, "UNCOMMON": 25.0, "RARE": 15.0, "EPIC": 10.0,
    "LEGENDARY": 6.0, "MYTHIC": 2.5, "SSR": 1.0, "UR": 0.5,
}
RARITY_VALUES = {
    "COMMON": 100, "UNCOMMON": 250, "RARE": 600, "EPIC": 1200,
    "LEGENDARY": 2500, "MYTHIC": 4500, "SSR": 8000, "UR": 15000,
}
RARITY_STARS = {"COMMON": 1, "UNCOMMON": 2, "RARE": 3, "EPIC": 4,
                "LEGENDARY": 5, "MYTHIC": 5, "SSR": 5, "UR": 5}
RARITY_BADGE = {"MYTHIC": "🔮", "SSR": "🌈", "UR": "👑"}


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _ids(name: str) -> set[int]:
    out: set[int] = set()
    for part in (os.getenv(name) or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def _weights() -> dict[str, float]:
    """RARITY_WEIGHTS="COMMON=40,UNCOMMON=25,..." overrides the defaults."""
    out = dict(DEFAULT_RARITY_WEIGHTS)
    for part in (os.getenv("RARITY_WEIGHTS") or "").split(","):
        name, _, val = part.partition("=")
        name = name.strip().upper()
        try:
            if name in out and float(val) >= 0:
                out[name] = float(val)
        except ValueError:
            pass
    return out


@dataclass
class Config:
    api_id: int = 0
    api_hash: str = ""
    bot_token: str = ""
    session_string: str = ""
    music_api_url: str = "https://musicapi.x007.workers.dev"
    music_engines: list[str] = field(default_factory=lambda: ["gaama", "seevn"])
    database_path: str = "data/bot.db"
    bot_name: str = "Zenith"
    owner_ids: set[int] = field(default_factory=set)
    waifu_default_interval: int = 120
    waifu_default_enabled: bool = True
    waifu_min_messages: int = 5
    image_api_url: str = ""
    image_api_key: str = ""
    image_model: str = "gpt-image-1"
    waifu_escape_seconds: int = 300
    auto_sync_waifus: bool = True
    max_queue_size: int = 100
    search_results: int = 6
    search_cache_ttl: int = 600
    rarity_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RARITY_WEIGHTS))
    pull_cost_gems: int = 100
    xp_cooldown_seconds: int = 30
    waifu_guess_cooldown: int = 5
    waifu_expire_seconds: int = 600
    waifu_active_window: int = 600
    anilist_pages: int = 12
    pity_threshold: int = 50
    ten_pull_discount: float = 0.9

    @classmethod
    def from_env(cls) -> "Config":
        engines = [e.strip() for e in (os.getenv("MUSIC_ENGINES") or "gaama,seevn").split(",") if e.strip()]
        return cls(
            api_id=_int("API_ID", 0),
            api_hash=os.getenv("API_HASH", ""),
            bot_token=os.getenv("BOT_TOKEN", ""),
            session_string=os.getenv("SESSION_STRING") or os.getenv("STRING_SESSION", ""),
            music_api_url=(os.getenv("MUSIC_API_URL") or "https://musicapi.x007.workers.dev").rstrip("/"),
            music_engines=engines or ["gaama"],
            database_path=os.getenv("DATABASE_PATH") or "data/bot.db",
            bot_name=os.getenv("BOT_NAME") or "Zenith",
            owner_ids=_ids("OWNER_IDS") | _ids("OWNER_ID"),
            waifu_default_interval=max(30, _int("WAIFU_DEFAULT_INTERVAL", 120)),
            waifu_default_enabled=_bool("WAIFU_DEFAULT_ENABLED", True),
            image_api_url=os.getenv("IMAGE_API_URL", ""),
            image_api_key=os.getenv("IMAGE_API_KEY", ""),
            image_model=os.getenv("IMAGE_MODEL") or "gpt-image-1",
            auto_sync_waifus=_bool("AUTO_SYNC_WAIFUS", True),
            waifu_min_messages=_int("WAIFU_MIN_MESSAGES", 5),
            anilist_pages=max(1, min(40, _int("ANILIST_PAGES", 12))),
            rarity_weights=_weights(),
        )

    def missing_required(self) -> list[str]:
        missing = []
        if not self.api_id:
            missing.append("API_ID")
        if not self.api_hash:
            missing.append("API_HASH")
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.session_string:
            missing.append("SESSION_STRING")
        return missing
