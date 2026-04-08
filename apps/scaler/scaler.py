"""ForgeChain queue-depth auto-scaler.

Watches Redis queue lengths for each role and scales Docker Compose
services up/down by calling the Docker API directly.

Scale-up:  queue depth >= FC_SCALER_UP_THRESHOLD  → add one container (up to max)
Scale-down: queue empty for FC_SCALER_DOWN_COOLDOWN seconds → remove one container (down to min)

Environment variables:
  REDIS_URL                   — Redis connection string
  COMPOSE_PROJECT_NAME        — Docker Compose project name (default: forgechain)
  FC_SCALER_INTERVAL          — Poll interval in seconds (default: 20)
  FC_SCALER_UP_THRESHOLD      — Queue depth that triggers scale-up (default: 2)
  FC_SCALER_DOWN_COOLDOWN     — Seconds of empty queue before scale-down (default: 180)
  FC_SCALER_MAX_<ROLE>        — Max containers per role, e.g. FC_SCALER_MAX_BACKEND_DEV=6
  FC_SCALER_MIN_<ROLE>        — Min containers per role (default: 1)
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Optional

import docker
import redis as _redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scaler] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────── #

ROLES = ["backend_dev", "frontend_dev", "qa_backend", "db_eng", "ai_eng", "sre", "ba"]

PROJECT      = os.getenv("COMPOSE_PROJECT_NAME", "forgechain")
INTERVAL     = int(os.getenv("FC_SCALER_INTERVAL",        "20"))
UP_THRESHOLD = int(os.getenv("FC_SCALER_UP_THRESHOLD",    "2"))
DOWN_COOL    = int(os.getenv("FC_SCALER_DOWN_COOLDOWN",   "180"))

def _max(role: str) -> int:
    return int(os.getenv(f"FC_SCALER_MAX_{role.upper()}", "4"))

def _min(role: str) -> int:
    return int(os.getenv(f"FC_SCALER_MIN_{role.upper()}", "1"))

# ── Docker helpers ────────────────────────────────────────────────────────── #

def _service_name(role: str) -> str:
    """Compose service label value for a role (matches com.forgechain.role label)."""
    return role

def _running_containers(client: docker.DockerClient, role: str) -> list:
    """Return all running containers for a given role label."""
    return client.containers.list(filters={
        "label": f"com.forgechain.role={role}",
        "status": "running",
    })

def _get_template_container(client: docker.DockerClient, role: str) -> Optional[docker.models.containers.Container]:
    """Return any existing container for this role to use as a config template."""
    all_containers = client.containers.list(
        all=True,
        filters={"label": f"com.forgechain.role={role}"},
    )
    return all_containers[0] if all_containers else None

def _scale_up(client: docker.DockerClient, role: str, current: int) -> bool:
    """Start one additional container cloned from the existing role container config."""
    template = _get_template_container(client, role)
    if template is None:
        logger.warning("[%s] No template container found — cannot scale up", role)
        return False

    attrs = template.attrs
    cfg   = attrs["Config"]
    hcfg  = attrs["HostConfig"]
    name  = f"{PROJECT}-fc-{role.replace('_','-')}-{current + 1}"

    try:
        container = client.containers.run(
            image=cfg["Image"],
            name=name,
            detach=True,
            environment=cfg.get("Env") or [],
            command=cfg.get("Cmd"),
            labels={**cfg.get("Labels", {}), "com.forgechain.scaler": "managed"},
            network_mode=f"container:{template.id}",  # share network stack
            volumes_from=[template.id],               # share volume mounts
            user=cfg.get("User", ""),
            working_dir=cfg.get("WorkingDir", ""),
            restart_policy=hcfg.get("RestartPolicy", {"Name": "unless-stopped"}),
        )
        logger.info("[%s] Scaled UP → %d containers (new: %s)", role, current + 1, container.short_id)
        return True
    except docker.errors.APIError as exc:
        logger.error("[%s] Scale-up failed: %s", role, exc)
        return False

def _scale_down(client: docker.DockerClient, role: str, containers: list) -> bool:
    """Stop the most recently started scaler-managed container for this role."""
    # Only remove containers we started (labelled com.forgechain.scaler=managed)
    managed = [
        c for c in containers
        if c.labels.get("com.forgechain.scaler") == "managed"
    ]
    if not managed:
        logger.debug("[%s] No scaler-managed containers to remove", role)
        return False

    # Remove the one started most recently
    target = sorted(managed, key=lambda c: c.attrs["Created"])[-1]
    try:
        target.stop(timeout=30)
        target.remove()
        logger.info("[%s] Scaled DOWN → %d containers (removed: %s)", role, len(containers) - 1, target.short_id)
        return True
    except docker.errors.APIError as exc:
        logger.error("[%s] Scale-down failed: %s", role, exc)
        return False

# ── Main loop ────────────────────────────────────────────────────────────── #

def run() -> None:
    redis_url = os.environ["REDIS_URL"]
    r = _redis.from_url(redis_url, decode_responses=True)
    client = docker.from_env()

    # Track how long each role's queue has been empty
    empty_since: dict[str, Optional[float]] = defaultdict(lambda: None)

    logger.info(
        "Scaler started — project=%s interval=%ds up_threshold=%d down_cooldown=%ds",
        PROJECT, INTERVAL, UP_THRESHOLD, DOWN_COOL,
    )

    while True:
        for role in ROLES:
            queue_key = f"forgechain:{role}"
            try:
                depth      = r.llen(queue_key)
                containers = _running_containers(client, role)
                current    = len(containers)
                max_rep    = _max(role)
                min_rep    = _min(role)

                if depth >= UP_THRESHOLD and current < max_rep:
                    logger.info(
                        "[%s] Queue depth %d >= threshold %d, current=%d max=%d → scaling up",
                        role, depth, UP_THRESHOLD, current, max_rep,
                    )
                    _scale_up(client, role, current)
                    empty_since[role] = None

                elif depth == 0:
                    now = time.monotonic()
                    if empty_since[role] is None:
                        empty_since[role] = now
                    elif now - empty_since[role] >= DOWN_COOL and current > min_rep:
                        logger.info(
                            "[%s] Queue empty for %.0fs (cooldown=%ds), current=%d min=%d → scaling down",
                            role, now - empty_since[role], DOWN_COOL, current, min_rep,
                        )
                        if _scale_down(client, role, containers):
                            empty_since[role] = now  # reset cooldown after each step-down

                else:
                    # Queue has work but not at threshold — reset empty timer
                    empty_since[role] = None

            except Exception as exc:
                logger.warning("[%s] Scaler poll error: %s", role, exc)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
