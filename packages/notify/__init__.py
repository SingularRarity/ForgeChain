"""ForgeChain notification package.

Supported channels:
  - Telegram  (Bot API — TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
  - Discord   (Webhook  — DISCORD_WEBHOOK_URL)

Usage:
    from notify import dispatcher
    await dispatcher.notify("job_review", task_id="abc", role="backend_dev", pr_url="https://...")
    await dispatcher.notify("job_approved", task_id="abc", role="backend_dev", reviewer="alice")
    await dispatcher.notify("job_rejected", task_id="abc", role="backend_dev", reason="needs work")
    await dispatcher.notify("prd_wave_done", prd_id="xyz", wave=2, total_waves=4)
    await dispatcher.notify("prd_done", prd_id="xyz", total_tasks=12)
    await dispatcher.notify("ema_degraded", role="backend_dev", quality=0.48, days=3)
"""

from .dispatcher import Dispatcher

dispatcher = Dispatcher()

__all__ = ["dispatcher", "Dispatcher"]
