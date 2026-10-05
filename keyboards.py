from __future__ import annotations

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M


def np_keyboard() -> M:
    return M([
        [B("⏸ Pause", callback_data="music:pause"), B("▶️ Resume", callback_data="music:resume")],
        [B("⏭ Skip", callback_data="music:skip"), B("⏹ Stop", callback_data="music:stop")],
        [B("📋 Queue", callback_data="queue:show"), B("📜 Lyrics", callback_data="music:lyrics")],
    ])


def search_keyboard(token: str, count: int) -> M:
    rows, row = [], []
    for i in range(count):
        row.append(B(str(i + 1), callback_data=f"music:sel:{token}:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("✖ Close", callback_data=f"music:close:{token}")])
    return M(rows)


def queue_keyboard(page: int, pages: int) -> M:
    nav = []
    if page > 1:
        nav.append(B("◀️", callback_data=f"queue:pg:{page - 1}"))
    nav.append(B(f"{page}/{pages}", callback_data="queue:noop"))
    if page < pages:
        nav.append(B("▶️", callback_data=f"queue:pg:{page + 1}"))
    return M([nav, [B("✖ Close", callback_data="queue:close")]])
