"""Project dataclass and registry CRUD.

Projects are stored in Redis for fast access and listed via the API.
Each project gets its own ChromaDB directory and optional skills path.

Redis keys:
  forgechain:registry:projects          — sorted set (score=created_at, member=project_id)
  forgechain:registry:project:{id}      — hash with all project fields
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_PROJECTS_PATH = os.getenv("FORGECHAIN_PROJECTS_PATH", "/app/projects")
_PROJECTS_SET_KEY = "forgechain:registry:projects"
_PROJECT_TTL = 0  # no TTL — projects are permanent until deleted


@dataclass(frozen=True)
class Project:
    """A registered codebase managed by ForgeChain."""

    project_id: str
    name: str
    description: str
    repo: str                     # GitHub repo (org/name) — auto-detected or manual
    repo_path: str                # Absolute local path to the codebase (empty if remote-only)
    created_at: float
    active: bool = True

    @property
    def kb_path(self) -> str:
        """Absolute path to this project's ChromaDB directory."""
        return str(Path(_PROJECTS_PATH) / self.project_id / "knowledge_base")

    @property
    def skills_path(self) -> str:
        """Absolute path to this project's skills files."""
        return str(Path(_PROJECTS_PATH) / self.project_id / "skills")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kb_path"]     = self.kb_path
        d["skills_path"] = self.skills_path
        return d


class ProjectRegistry:
    """Async CRUD for the project registry."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(
        self,
        name: str,
        description: str = "",
        repo: str = "",
        repo_path: str = "",
        project_id: str | None = None,
    ) -> Project:
        """Register a new project, create its directory structure, return it."""
        pid = project_id or _slug(name)
        now = time.time()

        project = Project(
            project_id=pid,
            name=name,
            description=description,
            repo=repo,
            repo_path=str(Path(repo_path).resolve()) if repo_path else "",
            created_at=now,
        )

        # Ensure KB and skills directories exist
        for path in (project.kb_path, project.skills_path):
            Path(path).mkdir(parents=True, exist_ok=True)

        # Ensure shared directories exist too
        shared_kb = Path(_PROJECTS_PATH) / "shared" / "knowledge_base"
        shared_skills = Path(_PROJECTS_PATH) / "shared" / "skills"
        shared_kb.mkdir(parents=True, exist_ok=True)
        shared_skills.mkdir(parents=True, exist_ok=True)

        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            key = _project_key(pid)
            await redis.hset(key, mapping={k: str(v) for k, v in asdict(project).items()})
            await redis.zadd(_PROJECTS_SET_KEY, {pid: now})
        finally:
            await redis.aclose()

        logger.info("[registry] Created project %r at %s", pid, project.kb_path)
        return project

    async def update_active(self, project_id: str, active: bool) -> None:
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            await redis.hset(_project_key(project_id), "active", str(active))
        finally:
            await redis.aclose()

    async def update_repo(
        self,
        project_id: str,
        repo: str = "",
        repo_path: str = "",
    ) -> None:
        """Update the git remote and/or local path for a project."""
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            updates: dict[str, str] = {}
            if repo:
                updates["repo"] = repo
            if repo_path:
                updates["repo_path"] = repo_path
            if updates:
                await redis.hset(_project_key(project_id), mapping=updates)
        finally:
            await redis.aclose()

    async def delete(self, project_id: str) -> bool:
        """Remove project from registry (does NOT delete KB files from disk)."""
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            removed = await redis.zrem(_PROJECTS_SET_KEY, project_id)
            await redis.delete(_project_key(project_id))
            logger.info("[registry] Deleted project %r", project_id)
            return bool(removed)
        finally:
            await redis.aclose()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, project_id: str) -> Project | None:
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            data = await redis.hgetall(_project_key(project_id))
            return _deserialize(data) if data else None
        finally:
            await redis.aclose()

    async def list_all(self) -> list[Project]:
        """Return all projects sorted by creation time (newest first)."""
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            ids = await redis.zrevrange(_PROJECTS_SET_KEY, 0, -1)
            projects: list[Project] = []
            for pid in ids:
                data = await redis.hgetall(_project_key(pid))
                if data:
                    p = _deserialize(data)
                    if p:
                        projects.append(p)
            return projects
        finally:
            await redis.aclose()

    async def list_active(self) -> list[Project]:
        return [p for p in await self.list_all() if p.active]

    async def exists(self, project_id: str) -> bool:
        redis = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            return bool(await redis.exists(_project_key(project_id)))
        finally:
            await redis.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_key(project_id: str) -> str:
    return f"forgechain:registry:project:{project_id}"


def _slug(name: str) -> str:
    """Convert a project name to a safe project_id slug."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip())
    slug = slug.strip("_")[:40] or "project"
    return slug


def _deserialize(data: dict[str, str]) -> Project | None:
    try:
        return Project(
            project_id=data["project_id"],
            name=data.get("name", data["project_id"]),
            description=data.get("description", ""),
            repo=data.get("repo", ""),
            repo_path=data.get("repo_path", ""),
            created_at=float(data.get("created_at", 0)),
            active=data.get("active", "True") == "True",
        )
    except (KeyError, ValueError):
        return None


def project_kb_path(project_id: str) -> str:
    """Return the ChromaDB path for a given project_id (or 'shared')."""
    return str(Path(_PROJECTS_PATH) / project_id / "knowledge_base")
