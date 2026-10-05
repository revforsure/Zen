from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GroupSettings:
    chat_id: int
    title: str | None = None
    prefix: str = "/"
    lang: str = "en"
    waifu_enabled: bool = True
    waifu_interval: int = 120
    gacha_enabled: bool = True
    music_enabled: bool = True
    maintenance: bool = False

    @classmethod
    def from_row(cls, row: dict) -> "GroupSettings":
        return cls(chat_id=row["chat_id"], title=row.get("title"), prefix=row.get("prefix") or "/",
                   lang=row.get("lang") or "en", waifu_enabled=bool(row["waifu_enabled"]),
                   waifu_interval=int(row["waifu_interval"]), gacha_enabled=bool(row["gacha_enabled"]),
                   music_enabled=bool(row["music_enabled"]), maintenance=bool(row["maintenance"]))
