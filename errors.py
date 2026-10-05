"""Exceptions whose message is safe to show to end users."""


class BotError(Exception):
    """Base class: str(exc) is a user-friendly message."""

    @property
    def user_message(self) -> str:
        return str(self.args[0]) if self.args else "Something went wrong."
