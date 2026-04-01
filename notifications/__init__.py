"""
Notifications — Sistema de notificaciones para BotStrike.

Soporta Telegram (extensible a Discord, etc.).
Si no hay token configurado, todas las notificaciones son no-op.
"""
from __future__ import annotations
from config.settings import Settings
from notifications.telegram import TelegramNotifier, NullNotifier


def get_notifier(settings: Settings) -> TelegramNotifier:
    """Factory: retorna TelegramNotifier real o NullNotifier (no-op)."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if token and chat_id:
        return TelegramNotifier(token, chat_id)
    return NullNotifier()
