"""Telegram Bot API notifier.

Requires:
  TELEGRAM_BOT_TOKEN — from @BotFather
  TELEGRAM_CHAT_ID   — channel/group/user ID to send to
                       (use @username for public channels, numeric ID for private)

Messages are sent as MarkdownV2. Special chars are escaped automatically.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT  = 10.0

# MarkdownV2 reserved characters that must be escaped
_MDV2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def _escape(text: str) -> str:
    """Escape all MarkdownV2 reserved characters."""
    return re.sub(r"([" + re.escape(_MDV2_SPECIAL) + r"])", r"\\\1", str(text))


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token   = token
        self._chat_id = chat_id
        self._url     = _API_BASE.format(token=token)

    async def send(self, message: str) -> None:
        """Send a MarkdownV2 message. Silently logs on failure — never raises."""
        payload = {
            "chat_id":    self._chat_id,
            "text":       message,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(self._url, json=payload)
                if not resp.is_success:
                    logger.warning(
                        "[telegram] Send failed: %s — %s",
                        resp.status_code, resp.text[:200],
                    )
        except Exception as exc:
            logger.warning("[telegram] Send error: %s", exc)

    @staticmethod
    def escape(text: str) -> str:
        return _escape(text)
