from __future__ import annotations

from dataclasses import dataclass


def level_for_xp(xp: int) -> int:
    return int((max(xp, 0) / 100) ** 0.5) + 1


def xp_for_level(level: int) -> int:
    return 100 * (max(level, 1) - 1) ** 2


def level_progress(xp: int) -> tuple[int, int, int]:
    """(level, xp into this level, xp needed for next level)."""
    lvl = level_for_xp(xp)
    base = xp_for_level(lvl)
    return lvl, xp - base, xp_for_level(lvl + 1) - base


@dataclass
class UserProfile:
    user_id: int
    username: str | None
    first_name: str | None
    xp: int
    level: int
    coins: int
    gems: int
    songs_played: int
    waifus: int
    achievements: int
    streak: int
    favorite_waifu: str | None = None

    @property
    def display(self) -> str:
        return f"@{self.username}" if self.username else (self.first_name or "User")
