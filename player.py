"""Per-chat playback engine. Voice-chat specifics live in PyTgCallsBackend only,
so the queue/advance/error logic is backend-agnostic and unit-testable."""
from __future__ import annotations

import asyncio
import inspect
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from models.music import Track
from services.music_api import MusicAPI, MusicAPIError
from services.queue import GroupQueue, QueueError, QueueManager

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
QUICK_END_SECONDS = 3.0


class VoiceError(Exception):
    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class PlaybackBackend(Protocol):
    async def start(self, on_end: Callable[[int], Awaitable[None]]) -> None: ...
    async def play(self, chat_id: int, url: str, kind: str, seek: int, volume: int) -> None: ...
    async def pause(self, chat_id: int) -> None: ...
    async def resume(self, chat_id: int) -> None: ...
    async def stop(self, chat_id: int) -> None: ...
    async def set_volume(self, chat_id: int, volume: int) -> None: ...


def ffmpeg_available() -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    try:
        subprocess.run([exe, "-version"], capture_output=True, timeout=10, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class PyTgCallsBackend:
    """Adapter over py-tgcalls 2.x. Imports are lazy so the rest of the bot (and tests)
    work without it. NOTE: written against the documented 2.x API; verify against the
    version pip installs on your device (see README)."""

    def __init__(self, assistant_client):
        from pytgcalls import PyTgCalls  # noqa: WPS433
        self._call = PyTgCalls(assistant_client)

    async def start(self, on_end: Callable[[int], Awaitable[None]]) -> None:
        from pytgcalls import filters
        flt = filters.stream_end
        if inspect.isfunction(flt):
            flt = flt()

        @self._call.on_update(flt)
        async def _stream_end(_, update):  # noqa: ANN001
            await on_end(update.chat_id)

        await self._call.start()

    @staticmethod
    def _translate(exc: Exception) -> VoiceError:
        name = type(exc).__name__
        if name in {"NoActiveGroupCall", "GroupCallNotFound"}:
            return VoiceError("There is no active voice chat. Start a voice chat in this group first.", fatal=True)
        if name in {"ChatAdminRequired", "ChannelPrivate", "NoAudioSourceFound"}:
            return VoiceError("I could not join the voice chat (check the assistant's permissions).", fatal=True)
        if name in {"TelegramServerError", "ConnectionNotFound", "NotInCallError"}:
            return VoiceError("The voice connection dropped. Try again.")
        return VoiceError("Playback failed for this track.")

    async def play(self, chat_id: int, url: str, kind: str, seek: int, volume: int) -> None:
        from pytgcalls.types import AudioQuality, MediaStream
        kwargs = {"audio_parameters": AudioQuality.HIGH, "video_flags": MediaStream.Flags.IGNORE}
        if seek > 0:
            kwargs["ffmpeg_parameters"] = f"-ss {int(seek)}"
        try:
            await self._call.play(chat_id, MediaStream(url, **kwargs))
            if volume != 100:
                await self._call.change_volume_call(chat_id, volume)
        except Exception as exc:  # noqa: BLE001
            log.warning("Voice play failed: %s", type(exc).__name__)
            raise self._translate(exc) from exc

    async def pause(self, chat_id: int) -> None:
        try:
            await self._call.pause(chat_id)
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    async def resume(self, chat_id: int) -> None:
        try:
            await self._call.resume(chat_id)
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    async def stop(self, chat_id: int) -> None:
        try:
            await self._call.leave_call(chat_id)
        except Exception as exc:  # noqa: BLE001
            log.info("leave_call ignored: %s", type(exc).__name__)

    async def set_volume(self, chat_id: int, volume: int) -> None:
        try:
            await self._call.change_volume_call(chat_id, volume)
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc


@dataclass
class ChatState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    status: str = "idle"  # idle | playing | paused
    started_at: float = 0.0
    quick_ends: int = 0
    restored: bool = False


Notifier = Callable[[int, str, "Track | None", str], Awaitable[None]]


class Player:
    """Events sent to `notifier(chat_id, kind, track, text)`:
    playing | failed | finished | error"""

    def __init__(self, queues: QueueManager, music: MusicAPI, db, backend: PlaybackBackend,
                 notifier: Notifier | None = None,
                 assistant_check: Callable[[int], Awaitable[None]] | None = None,
                 ffmpeg_check: Callable[[], bool] = ffmpeg_available):
        self.queues, self.music, self.db, self.backend = queues, music, db, backend
        self.notifier = notifier
        self.assistant_check = assistant_check
        self._ffmpeg_check = ffmpeg_check
        self._ffmpeg_ok: bool | None = None
        self._states: dict[int, ChatState] = {}

    # ---- plumbing --------------------------------------------------
    def state(self, chat_id: int) -> ChatState:
        st = self._states.get(chat_id)
        if st is None:
            st = self._states[chat_id] = ChatState()
        return st

    async def start(self) -> None:
        await self.backend.start(self.on_stream_end)

    async def _notify(self, chat_id: int, kind: str, track: Track | None, text: str = "") -> None:
        if self.notifier:
            try:
                await self.notifier(chat_id, kind, track, text)
            except Exception:
                log.exception("Notifier failed")

    async def _persist(self, chat_id: int) -> None:
        try:
            q = self.queues.get(chat_id)
            rows = []
            for t in q.snapshot():
                d = t.to_dict()
                d["stream_url"] = ""  # URLs may expire; always re-fetched
                rows.append(d)
            await self.db.save_queue(chat_id, rows)
        except Exception:
            log.exception("Could not persist queue")

    def _check_ffmpeg(self) -> None:
        if self._ffmpeg_ok is None:
            self._ffmpeg_ok = bool(self._ffmpeg_check())
        if not self._ffmpeg_ok:
            raise VoiceError("FFmpeg was not found. Install it with: pkg install ffmpeg -y", fatal=True)

    async def _restore(self, chat_id: int, st: ChatState, q: GroupQueue) -> None:
        if st.restored:
            return
        st.restored = True
        if q.current is not None or q.upcoming:
            return
        try:
            for raw in await self.db.load_queue(chat_id):
                q.add(Track.from_dict(raw), allow_duplicate=True)
        except Exception:
            log.exception("Queue restore failed")

    # ---- core ------------------------------------------------------
    async def _start_current(self, chat_id: int, seek: int = 0) -> bool:
        """Play q.current, skipping unplayable tracks. Caller holds the chat lock."""
        q, st = self.queues.get(chat_id), self.state(chat_id)
        failures = 0
        while q.current is not None:
            track = q.current
            try:
                self._check_ffmpeg()
                if self.assistant_check:
                    await self.assistant_check(chat_id)
                await self.music.fetch_stream(track)
                await self.backend.play(chat_id, track.stream_url, track.stream_kind, seek, q.volume)
            except VoiceError as exc:
                if exc.fatal:
                    q.reset()
                    st.status = "idle"
                    await self._persist(chat_id)
                    await self._notify(chat_id, "error", track, str(exc))
                    return False
                failure = str(exc)
            except MusicAPIError as exc:
                failure = str(exc)
            except Exception:  # noqa: BLE001
                log.exception("Unexpected playback error")
                failure = "Playback failed for this track."
            else:
                st.status, st.started_at = "playing", time.monotonic()
                log.info("Playing: %s", track.title)
                try:
                    await self.db.add_history(chat_id, track.requester_id, track.title, track.artist,
                                              track.source, track.source_id)
                except Exception:
                    log.exception("History write failed")
                await self._persist(chat_id)
                await self._notify(chat_id, "playing", track, "")
                return True
            failures += 1
            await self._notify(chat_id, "failed", track, failure)
            q.advance() if not q.loop_current else setattr(q, "loop_current", False)
            if failures >= MAX_CONSECUTIVE_FAILURES and q.current is not None:
                q.reset()
                await self._notify(chat_id, "error", None, "Too many tracks failed in a row, so I stopped.")
                break
        await self._finish(chat_id)
        return False

    async def _finish(self, chat_id: int) -> None:
        st = self.state(chat_id)
        st.status = "idle"
        await self.backend.stop(chat_id)
        await self._persist(chat_id)
        log.info("Queue finished for chat %s", chat_id)
        await self._notify(chat_id, "finished", None, "")

    # ---- public API ------------------------------------------------
    async def enqueue(self, chat_id: int, track: Track) -> int:
        """Add a track; starts playback if idle. Returns queue position (0 = now)."""
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            await self._restore(chat_id, st, q)
            pos = q.add(track)
            if q.current is track and st.status == "idle":
                await self._start_current(chat_id)
            else:
                await self._persist(chat_id)
            return pos

    async def on_stream_end(self, chat_id: int) -> None:
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            if st.status != "playing":
                return
            elapsed = time.monotonic() - st.started_at
            st.quick_ends = st.quick_ends + 1 if elapsed < QUICK_END_SECONDS else 0
            log.info("Track ended in chat %s", chat_id)
            if st.quick_ends >= MAX_CONSECUTIVE_FAILURES:
                st.quick_ends = 0
                q.reset()
                await self._notify(chat_id, "error", None, "Streams keep ending instantly, so I stopped.")
                await self._finish(chat_id)
                return
            q.advance()
            log.info("Queue advanced in chat %s", chat_id)
            if q.current is None:
                await self._finish(chat_id)
            else:
                await self._start_current(chat_id)

    async def skip(self, chat_id: int) -> Track | None:
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            if q.current is None:
                raise QueueError("Nothing is playing.")
            q.loop_current = False
            q.advance()
            if q.current is None:
                await self._finish(chat_id)
                return None
            await self._start_current(chat_id)
            return q.current

    async def previous(self, chat_id: int) -> Track | None:
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            q.previous()
            await self._start_current(chat_id)
            return q.current

    async def stop(self, chat_id: int) -> None:
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            q.reset()
            st.status = "idle"
            await self.backend.stop(chat_id)
            await self._persist(chat_id)

    async def pause(self, chat_id: int) -> None:
        st = self.state(chat_id)
        async with st.lock:
            if st.status != "playing":
                raise QueueError("Nothing is playing." if st.status == "idle" else "Already paused.")
            await self.backend.pause(chat_id)
            st.status = "paused"

    async def resume(self, chat_id: int) -> None:
        st = self.state(chat_id)
        async with st.lock:
            if st.status != "paused":
                raise QueueError("Nothing is paused.")
            await self.backend.resume(chat_id)
            st.status = "playing"

    async def set_volume(self, chat_id: int, volume: int) -> None:
        if not 1 <= volume <= 200:
            raise QueueError("Volume must be between 1 and 200.")
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            q.volume = volume
            if st.status != "idle":
                await self.backend.set_volume(chat_id, volume)

    async def seek(self, chat_id: int, seconds: int) -> None:
        q, st = self.queues.get(chat_id), self.state(chat_id)
        if seconds < 0 or seconds > 6 * 3600:
            raise QueueError("That seek position is not valid.")
        async with st.lock:
            if q.current is None:
                raise QueueError("Nothing is playing.")
            ok = await self._start_current(chat_id, seek=seconds)
            if not ok:
                raise QueueError("Could not seek in this track.")

    async def replay(self, chat_id: int) -> Track:
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            track = q.replay()
            await self._start_current(chat_id)
            return track

    async def mutate_queue(self, chat_id: int, action: str, arg: int = 0):
        """remove / clear / shuffle with persistence, under the chat lock."""
        q, st = self.queues.get(chat_id), self.state(chat_id)
        async with st.lock:
            if action == "remove":
                result = q.remove(arg)
            elif action == "clear":
                result = q.clear()
            elif action == "shuffle":
                q.shuffle()
                result = None
            else:
                raise ValueError(action)
            await self._persist(chat_id)
            return result
