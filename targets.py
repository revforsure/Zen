"""Resolve who a command is aimed at: reply > @username (known users) > numeric id."""
from __future__ import annotations

from utils.formatting import clean


async def resolve_target(ctx, message, arg: str | None = None, use_reply: bool = True):
    """Returns (user_id, display_name, remaining_text) or (None, None, text)."""
    parts = (arg if arg is not None else " ".join(message.command[1:])).split()
    reply = message.reply_to_message
    if use_reply and reply and reply.from_user and not reply.from_user.is_bot:
        u = reply.from_user
        await ctx.db.ensure_user(u.id, u.username, u.first_name)
        return u.id, display(u.username, u.first_name), " ".join(parts)
    if not parts:
        return None, None, ""
    first, rest = parts[0], " ".join(parts[1:])
    if first.startswith("@") and len(first) > 2:
        row = await ctx.db.fetchone("SELECT user_id, username, first_name FROM users WHERE lower(username)=lower(?)",
                                    (first[1:],))
        if row:
            return row["user_id"], display(row["username"], row["first_name"]), rest
        return None, None, rest
    if first.isdigit() and len(first) >= 5:
        row = await ctx.db.fetchone("SELECT user_id, username, first_name FROM users WHERE user_id=?", (int(first),))
        if row:
            return row["user_id"], display(row["username"], row["first_name"]), rest
    return None, None, " ".join(parts)


def display(username: str | None, first_name: str | None) -> str:
    return "@" + clean(username, 40) if username else clean(first_name or "User", 40)
