"""Per-chat music queues. Pure in-memory logic; persistence handled by the caller."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from models.music import Track
from services.errors import BotError


class QueueError(BotError):
    pass


@dataclass
class GroupQueue:
    chat_id: int
    max_size: int = 100
    current: Track | None = None
    upcoming: list[Track] = field(default_factory=list)
    played: list[Track] = field(default_factory=list)  # for "previous"
    volume: int = 100
    loop_current: bool = False

    def is_duplicate(self, track: Track) -> bool:
        if self.current and self.current.key == track.key:
            return True
        return any(t.key == track.key for t in self.upcoming)

    def add(self, track: Track, allow_duplicate: bool = False) -> int:
        """Append; returns 1-based position (0 means it will play right now)."""
        if not allow_duplicate and self.is_duplicate(track):
            raise QueueError("That track is already in the queue.")
        if len(self.upcoming) >= self.max_size:
            raise QueueError("The queue is full.")
        if self.current is None:
            self.current = track
            return 0
        self.upcoming.append(track)
        return len(self.upcoming)

    def remove(self, position: int) -> Track:
        if not 1 <= position <= len(self.upcoming):
            raise QueueError("No track at that position.")
        return self.upcoming.pop(position - 1)

    def clear(self) -> int:
        n = len(self.upcoming)
        self.upcoming.clear()
        return n

    def shuffle(self) -> None:
        if len(self.upcoming) < 2:
            raise QueueError("Not enough tracks to shuffle.")
        random.shuffle(self.upcoming)

    def peek_next(self) -> Track | None:
        return self.upcoming[0] if self.upcoming else None

    def advance(self) -> Track | None:
        """Move to the next track (skip / track ended). Returns new current or None."""
        if self.current is not None:
            if self.loop_current:
                return self.current
            self.played.append(self.current)
            del self.played[:-20]
        self.current = self.upcoming.pop(0) if self.upcoming else None
        return self.current

    def previous(self) -> Track | None:
        if not self.played:
            raise QueueError("There is no previous track.")
        if self.current is not None:
            self.upcoming.insert(0, self.current)
        self.current = self.played.pop()
        return self.current

    def replay(self) -> Track:
        if self.current is None:
            raise QueueError("Nothing is playing.")
        return self.current

    def reset(self) -> None:
        self.current = None
        self.upcoming.clear()
        self.played.clear()

    def snapshot(self) -> list[Track]:
        return ([self.current] if self.current else []) + list(self.upcoming)


class QueueManager:
    """Independent queue per Telegram chat."""

    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self._queues: dict[int, GroupQueue] = {}

    def get(self, chat_id: int) -> GroupQueue:
        q = self._queues.get(chat_id)
        if q is None:
            q = self._queues[chat_id] = GroupQueue(chat_id, self.max_size)
        return q

    def drop(self, chat_id: int) -> None:
        self._queues.pop(chat_id, None)

    def active_chats(self) -> list[int]:
        return [c for c, q in self._queues.items() if q.current is not None]
