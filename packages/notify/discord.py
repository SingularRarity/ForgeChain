"""Discord Webhook notifier.

Requires:
  DISCORD_WEBHOOK_URL — from Server Settings → Integrations → Webhooks

Messages are sent as rich embeds with a title, description, colour, and
optional fields. Colour encodes event severity at a glance.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# Embed colour palette (decimal RGB)
COLOUR_INFO    = 0x5865F2   # blurple
COLOUR_SUCCESS = 0x57F287   # green
COLOUR_WARNING = 0xFEE75C   # yellow
COLOUR_DANGER  = 0xED4245   # red
COLOUR_NEUTRAL = 0x99AAB5   # grey


class DiscordNotifier:
    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    async def send(
        self,
        title: str,
        description: str,
        colour: int = COLOUR_INFO,
        fields: Optional[list[dict]] = None,
    ) -> None:
        """Post a rich embed to the Discord webhook. Never raises."""
        embed: dict = {
            "title":       title,
            "description": description,
            "color":       colour,
        }
        if fields:
            embed["fields"] = fields  # [{"name": "...", "value": "...", "inline": True}]

        payload = {"embeds": [embed]}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(self._url, json=payload)
                if not resp.is_success:
                    logger.warning(
                        "[discord] Send failed: %s — %s",
                        resp.status_code, resp.text[:200],
                    )
        except Exception as exc:
            logger.warning("[discord] Send error: %s", exc)
