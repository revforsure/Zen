from __future__ import annotations

import logging

from utils.tg import reply

log = logging.getLogger(__name__)
FEATURES = {"music": "music_enabled", "gacha": "gacha_enabled", "waifu": "waifu_enabled"}
GROUP_TYPES = {"GROUP", "SUPERGROUP"}


def is_owner(ctx, user_id: int) -> bool:
    return user_id in ctx.config.owner_ids


async def is_chat_admin(ctx, chat_id: int, user_id: int) -> bool:
    if is_owner(ctx, user_id):
        return True
    key = (chat_id, user_id)
    cached = ctx.admin_cache.get(key)
    if cached is not None:
        return cached
    try:
        member = await ctx.bot.get_chat_member(chat_id, user_id)
        result = member.status.name in {"OWNER", "ADMINISTRATOR"}
    except Exception as exc:  # noqa: BLE001
        log.info("Admin check failed: %s", type(exc).__name__)
        result = False
    ctx.admin_cache.set(key, result)
    return result


async def can_control(ctx, chat_id: int, user_id: int) -> bool:
    """Admins, bot owners, or whoever requested the current track."""
    if await is_chat_admin(ctx, chat_id, user_id):
        return True
    cur = ctx.player.queues.get(chat_id).current
    return bool(cur and cur.requester_id == user_id)


async def _group_row(ctx, chat) -> dict:
    cfg = ctx.config
    await ctx.db.ensure_group(chat.id, chat.title, cfg.waifu_default_interval, cfg.waifu_default_enabled)
    return await ctx.db.fetchone("SELECT * FROM groups WHERE chat_id=?", (chat.id,))


def _blocked(ctx, row: dict, user_id: int, feature: str | None) -> str | None:
    if row["maintenance"] and not is_owner(ctx, user_id):
        return "🛠 The bot is in maintenance mode. Please try again later."
    if feature and not row[FEATURES[feature]]:
        return f"🚫 {feature.capitalize()} is disabled in this group."
    return None


async def group_gate(ctx, message, feature: str | None = None) -> bool:
    chat, user = message.chat, message.from_user
    if chat.type.name not in GROUP_TYPES:
        await reply(message, "👥 This command only works in groups.")
        return False
    if user is None:
        return False
    await ctx.db.ensure_user(user.id, user.username, user.first_name)
    row = await _group_row(ctx, chat)
    problem = _blocked(ctx, row, user.id, feature)
    if problem:
        await reply(message, problem)
        return False
    return True


async def cb_gate(ctx, cb, feature: str | None = None) -> bool:
    chat = cb.message.chat if cb.message else None
    if chat is None or chat.type.name not in GROUP_TYPES:
        await cb.answer("This only works in groups.", show_alert=True)
        return False
    await ctx.db.ensure_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    row = await _group_row(ctx, chat)
    problem = _blocked(ctx, row, cb.from_user.id, feature)
    if problem:
        await cb.answer(problem, show_alert=True)
        return False
    return True


async def require_admin(ctx, message) -> bool:
    user = message.from_user
    if user and await is_chat_admin(ctx, message.chat.id, user.id):
        return True
    await reply(message, "🔒 Only group admins can use this command.")
    return False


async def require_owner(ctx, message) -> bool:
    user = message.from_user
    if user and is_owner(ctx, user.id):
        return True
    await reply(message, "🔒 This command is restricted to the bot owner.")
    return False


async def soft_gate(ctx, message, feature: str | None = None) -> bool:
    """Like group_gate, but also allows private chats (for economy/profile/gacha commands)."""
    if message.chat.type.name == "PRIVATE":
        user = message.from_user
        if user is None:
            return False
        await ctx.db.ensure_user(user.id, user.username, user.first_name)
        return True
    return await group_gate(ctx, message, feature)
