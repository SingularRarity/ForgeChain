"""Unified notification dispatcher.

Reads environment variables once at construction time. Channels that have
no credentials configured are silently skipped — the rest are notified in
parallel via asyncio.gather.

Event types and their kwargs:
  job_review    — task_id, role, tier, pr_url, description
  job_approved  — task_id, role, reviewer, comment
  job_rejected  — task_id, role, reviewer, reason
  prd_wave_done — prd_id, wave, total_waves, tasks_in_wave
  prd_done      — prd_id, total_tasks, project
  ema_degraded  — role, quality, days_below
  coverage_gap  — role, avg_score, threshold
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .telegram import TelegramNotifier
from .discord  import (
    DiscordNotifier,
    COLOUR_INFO, COLOUR_SUCCESS, COLOUR_WARNING, COLOUR_DANGER, COLOUR_NEUTRAL,
)

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self) -> None:
        self._telegram = self._init_telegram()
        self._discord  = self._init_discord()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def notify(self, event: str, **kwargs: Any) -> None:
        """Fire-and-forget: builds messages for all live channels in parallel."""
        if not self._telegram and not self._discord:
            return

        builder = _EVENT_BUILDERS.get(event)
        if builder is None:
            logger.debug("[notify] Unknown event %r — skipped", event)
            return

        tg_msg, dc_title, dc_desc, dc_colour, dc_fields = builder(**kwargs)

        tasks = []
        if self._telegram and tg_msg:
            tasks.append(self._telegram.send(tg_msg))
        if self._discord and dc_title:
            tasks.append(self._discord.send(dc_title, dc_desc, dc_colour, dc_fields))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("[notify] Channel error: %s", r)

    # ------------------------------------------------------------------ #
    # Init helpers                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _init_telegram() -> TelegramNotifier | None:
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if token and chat_id:
            logger.info("[notify] Telegram enabled (chat_id=%s)", chat_id)
            return TelegramNotifier(token, chat_id)
        return None

    @staticmethod
    def _init_discord() -> DiscordNotifier | None:
        url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        if url:
            logger.info("[notify] Discord enabled")
            return DiscordNotifier(url)
        return None


# ---------------------------------------------------------------------------
# Message builders — return (tg_msg, dc_title, dc_desc, dc_colour, dc_fields)
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return TelegramNotifier.escape(text)


def _build_job_review(
    task_id: str = "",
    role: str = "",
    tier: str = "",
    pr_url: str = "",
    description: str = "",
) -> tuple:
    short_id = task_id[:8]
    short_desc = (description[:120] + "…") if len(description) > 120 else description

    tg = (
        f"🔍 *PR Ready for Review*\n"
        f"Role: `{_esc(role)}` \\| Tier: `{_esc(tier)}`\n"
        f"Task: `{_esc(short_id)}`\n"
        f"{_esc(short_desc)}"
        + (f"\n[Open PR]({_esc(pr_url)})" if pr_url else "")
    )

    fields = [
        {"name": "Role",  "value": f"`{role}`",     "inline": True},
        {"name": "Tier",  "value": f"`{tier}`",     "inline": True},
        {"name": "Task",  "value": f"`{short_id}`", "inline": True},
    ]
    if pr_url:
        fields.append({"name": "PR", "value": f"[View]({pr_url})", "inline": False})

    return tg, "🔍 PR Ready for Review", short_desc, COLOUR_INFO, fields


def _build_job_approved(
    task_id: str = "",
    role: str = "",
    reviewer: str = "",
    comment: str = "",
) -> tuple:
    short_id = task_id[:8]
    tg = (
        f"✅ *Job Approved*\n"
        f"Role: `{_esc(role)}` \\| Task: `{_esc(short_id)}`\n"
        f"Reviewer: {_esc(reviewer)}"
        + (f"\n_{_esc(comment)}_" if comment else "")
    )
    fields = [
        {"name": "Role",     "value": f"`{role}`",     "inline": True},
        {"name": "Task",     "value": f"`{short_id}`", "inline": True},
        {"name": "Reviewer", "value": reviewer,        "inline": True},
    ]
    desc = comment or f"Approved by {reviewer}"
    return tg, "✅ Job Approved", desc, COLOUR_SUCCESS, fields


def _build_job_rejected(
    task_id: str = "",
    role: str = "",
    reviewer: str = "",
    reason: str = "",
) -> tuple:
    short_id = task_id[:8]
    tg = (
        f"❌ *Job Rejected*\n"
        f"Role: `{_esc(role)}` \\| Task: `{_esc(short_id)}`\n"
        f"Reviewer: {_esc(reviewer)}"
        + (f"\nReason: _{_esc(reason)}_" if reason else "")
    )
    fields = [
        {"name": "Role",     "value": f"`{role}`",     "inline": True},
        {"name": "Task",     "value": f"`{short_id}`", "inline": True},
        {"name": "Reviewer", "value": reviewer,        "inline": True},
    ]
    if reason:
        fields.append({"name": "Reason", "value": reason, "inline": False})
    return tg, "❌ Job Rejected", reason or f"Rejected by {reviewer}", COLOUR_DANGER, fields


def _build_prd_wave_done(
    prd_id: str = "",
    wave: int = 0,
    total_waves: int = 0,
    tasks_in_wave: int = 0,
) -> tuple:
    short_id = prd_id[:8]
    tg = (
        f"⚡ *PRD Wave {_esc(str(wave))} Complete*\n"
        f"PRD: `{_esc(short_id)}` \\| Wave {_esc(str(wave))}/{_esc(str(total_waves))}\n"
        f"{_esc(str(tasks_in_wave))} task\\(s\\) completed"
    )
    desc = f"Wave {wave} of {total_waves} finished — {tasks_in_wave} task(s) done"
    fields = [
        {"name": "PRD",          "value": f"`{short_id}`",            "inline": True},
        {"name": "Wave",         "value": f"{wave} / {total_waves}",  "inline": True},
        {"name": "Tasks in wave","value": str(tasks_in_wave),         "inline": True},
    ]
    return tg, f"⚡ PRD Wave {wave} Complete", desc, COLOUR_INFO, fields


def _build_prd_done(
    prd_id: str = "",
    total_tasks: int = 0,
    project: str = "",
) -> tuple:
    short_id = prd_id[:8]
    proj_str = f" \\(project: `{_esc(project)}`\\)" if project else ""
    tg = (
        f"🎉 *PRD Execution Complete*\n"
        f"PRD: `{_esc(short_id)}`{proj_str}\n"
        f"All {_esc(str(total_tasks))} tasks finished"
    )
    desc = f"All {total_tasks} tasks completed" + (f" for project `{project}`" if project else "")
    fields = [
        {"name": "PRD",         "value": f"`{short_id}`",   "inline": True},
        {"name": "Total Tasks", "value": str(total_tasks),  "inline": True},
    ]
    if project:
        fields.append({"name": "Project", "value": f"`{project}`", "inline": True})
    return tg, "🎉 PRD Execution Complete", desc, COLOUR_SUCCESS, fields


def _build_ema_degraded(
    role: str = "",
    quality: float = 0.0,
    days_below: int = 0,
) -> tuple:
    q_pct = f"{quality * 100:.1f}%"
    tg = (
        f"⚠️ *Worker Quality Alert*\n"
        f"Role `{_esc(role)}` has been below quality threshold for "
        f"{_esc(str(days_below))} day\\(s\\)\n"
        f"Current quality: {_esc(q_pct)}"
    )
    desc = f"`{role}` quality at {q_pct} — below threshold for {days_below} day(s). Consider ingesting fresh knowledge."
    fields = [
        {"name": "Role",      "value": f"`{role}`",       "inline": True},
        {"name": "Quality",   "value": q_pct,             "inline": True},
        {"name": "Days below","value": str(days_below),   "inline": True},
    ]
    return tg, "⚠️ Worker Quality Alert", desc, COLOUR_WARNING, fields


def _build_coverage_gap(
    role: str = "",
    avg_score: float = 0.0,
    threshold: float = 0.25,
) -> tuple:
    score_str = f"{avg_score:.3f}"
    tg = (
        f"📉 *Knowledge Gap Detected*\n"
        f"Role `{_esc(role)}` avg retrieval score: {_esc(score_str)} "
        f"\\(threshold: {_esc(str(threshold))}\\)"
    )
    desc = f"`{role}` KB retrieval score {score_str} is below threshold {threshold}. Ingest more docs."
    fields = [
        {"name": "Role",      "value": f"`{role}`",   "inline": True},
        {"name": "Avg Score", "value": score_str,     "inline": True},
        {"name": "Threshold", "value": str(threshold),"inline": True},
    ]
    return tg, "📉 Knowledge Gap Detected", desc, COLOUR_NEUTRAL, fields


_EVENT_BUILDERS = {
    "job_review":    _build_job_review,
    "job_approved":  _build_job_approved,
    "job_rejected":  _build_job_rejected,
    "prd_wave_done": _build_prd_wave_done,
    "prd_done":      _build_prd_done,
    "ema_degraded":  _build_ema_degraded,
    "coverage_gap":  _build_coverage_gap,
}
