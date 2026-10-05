"""Centralised user-facing strings. Add a language by adding a dict to STRINGS."""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "start_tag": "🎧 **Music • Anime • Gacha • Fun**",
        "start_body": ("Your all-in-one Telegram entertainment bot.\n\n"
                       "🎵 Stream music\n🌸 Collect anime characters\n🎴 Pull gacha\n"
                       "💰 Build your economy\n🏆 Complete achievements\n🎮 Play mini-games"),
        "help_title": "📚 **HELP**",
        "help_pick": "__Pick a category below.__",
        "groups_only": "👥 This command only works in groups.",
        "admins_only": "🔒 Only group admins can use this command.",
        "owner_only": "🔒 This command is restricted to the bot owner.",
        "generic_error": "⚠️ Something went wrong. Please try again in a moment.",
        "slow_down": "⏳ Slow down — try again in **{s}s**.",
    }
}
SUPPORTED = tuple(STRINGS)


def t(key: str, lang: str = "en", **kw) -> str:
    text = STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"].get(key) or key
    return text.format(**kw) if kw else text
