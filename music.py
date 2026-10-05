from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Track:
    title: str
    artist: str = "Unknown"
    album: str = ""
    duration: int = 0  # seconds, 0 = unknown (the API does not provide it)
    thumbnail: str = ""
    source: str = ""  # search engine name
    source_id: str = ""
    stream_url: str = ""
    stream_kind: str = ""  # "hls" | "audio" | "video"
    resolved_at: float = 0.0  # unix time the stream_url was fetched
    requester_id: int = 0
    requester_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.source_id)
