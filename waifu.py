from __future__ import annotations

import json
from dataclasses import dataclass, field

from config import RARITY_STARS


@dataclass
class Waifu:
    id: int
    name: str
    anime: str
    image_url: str = ""
    rarity: str = "COMMON"
    aliases: list[str] = field(default_factory=list)
    value: int = 100
    source: str = "manual"
    source_id: str = ""
    release_info: str = ""
    tags: str = ""
    popularity: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "Waifu":
        try:
            aliases = [a for a in json.loads(row.get("aliases") or "[]") if isinstance(a, str)]
        except ValueError:
            aliases = []
        return cls(id=row["id"], name=row["name"], anime=row["anime"], image_url=row.get("image_url") or "",
                   rarity=row.get("rarity") or "COMMON", aliases=aliases, value=int(row.get("value") or 0),
                   source=row.get("source") or "manual", source_id=row.get("source_id") or "",
                   release_info=row.get("release_info") or "", tags=row.get("tags") or "",
                   popularity=int(row.get("popularity") or 0))

    @property
    def stars(self) -> str:
        return "⭐" * RARITY_STARS.get(self.rarity, 1)

    @property
    def rarity_label(self) -> str:
        return self.rarity if self.rarity in {"SSR", "UR"} else self.rarity.title()
