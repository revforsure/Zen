"""Message text builders. Telegram Markdown: **bold**, __italic__, `code`, ~~strike~~."""
from __future__ import annotations

import re

from config import RARITY_BADGE, RARITY_STARS
from models.music import Track
from utils.helpers import format_duration

SEP = "━━━━━━━━━━━━━━━━━━"
_UNSAFE = re.compile(r"[*_`~\[\]\\|<>]")


def clean(text: object, limit: int = 80) -> str:
    """Make user/API text safe to embed inside Markdown."""
    s = _UNSAFE.sub("", str(text or "")).strip()
    s = " ".join(s.split())
    return s[: limit - 1] + "…" if len(s) > limit else s


def header(title: str) -> str:
    return f"╭━━━━━━━━━━━━━━━━━━╮\n      {title}\n╰━━━━━━━━━━━━━━━━━━╯"


def mention(user) -> str:
    if getattr(user, "username", None):
        return "@" + clean(user.username, 40)
    return clean(getattr(user, "first_name", "") or "User", 40)


def np_text(track: Track, queue_len: int = 0, paused: bool = False) -> str:
    lines = [header("⏸ **PAUSED**" if paused else "🎧 **NOW PLAYING**"), "", f"**{clean(track.title, 100)}**"]
    if track.artist and track.artist != "Unknown":
        lines.append(f"__{clean(track.artist, 100)}__")
    if track.album:
        lines.append(f"💿 {clean(track.album, 80)}")
    if track.duration:
        lines.append(f"🎵 `{format_duration(track.duration)}`")
    lines += ["", f"👤 Requested by **{clean(track.requester_name or 'Unknown', 40)}**"]
    if queue_len:
        lines.append(f"📋 Up next: **{queue_len}** track{'s' if queue_len != 1 else ''}")
    lines.append(SEP)
    return "\n".join(lines)


def results_text(query: str, tracks: list[Track]) -> str:
    lines = [f"🔎 **Results for** `{clean(query, 60)}`", ""]
    for i, t in enumerate(tracks, 1):
        lines.append(f"**{i}.** {clean(t.title, 70)}")
        if t.album:
            lines.append(f"      💿 {clean(t.album, 50)}")
    lines += ["", "__Tap a number to add it to the queue.__"]
    return "\n".join(lines)


def queue_page(current: Track | None, upcoming: list[Track], page: int, per_page: int = 8) -> tuple[str, int, int]:
    pages = max(1, -(-len(upcoming) // per_page))
    page = min(max(page, 1), pages)
    lines = [header("📋 **QUEUE**"), ""]
    if current:
        lines.append(f"▶️ **{clean(current.title, 70)}**")
        lines.append(f"      👤 {clean(current.requester_name, 40)}")
    else:
        lines.append("__Nothing is playing.__")
    start = (page - 1) * per_page
    chunk = upcoming[start:start + per_page]
    if chunk:
        lines.append("")
    for i, t in enumerate(chunk, start + 1):
        lines.append(f"`{i}.` {clean(t.title, 60)} — __{clean(t.requester_name, 24)}__")
    lines += ["", f"{SEP}\nPage {page}/{pages} • {len(upcoming)} queued"]
    return "\n".join(lines), page, pages


def rarity_line(rarity: str) -> str:
    return f"{'⭐' * RARITY_STARS.get(rarity, 1)} {RARITY_BADGE.get(rarity, '')}{rarity.title() if rarity not in ('SSR', 'UR') else rarity}".replace("  ", " ").strip()


def num(n: int) -> str:
    return f"{int(n):,}"


def bar(value: int, total: int, width: int = 10) -> str:
    filled = 0 if total <= 0 else min(width, int(width * value / total))
    return "▰" * filled + "▱" * (width - filled)


def waifu_card(w, owner: str | None = None, extra: str = "") -> str:
    lines = [f"🌸 **{clean(w.name, 80)}**", "", f"📺 **{clean(w.anime, 80)}**", f"⭐ **{rarity_line(w.rarity)}**",
             "", f"💰 Value: **{num(w.value)}**"]
    if owner:
        lines += ["", f"👤 Owner: **{owner}**"]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)
