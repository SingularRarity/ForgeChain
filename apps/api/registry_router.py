"""Multi-app Registry API routes.

POST   /forgechain/projects             Register a new project
GET    /forgechain/projects             List all projects
GET    /forgechain/projects/{id}        Get project details
DELETE /forgechain/projects/{id}        Remove project from registry
GET    /forgechain/projects/{id}/gaps   Knowledge gaps for a specific project
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from registry.project import ProjectRegistry, Project
from registry.scanner import RepoScanner, ScanResult
from quant.coverage import CoverageTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forgechain", tags=["registry"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="Human-readable project name")
    description: str = Field("", max_length=500)
    repo: str = Field("", description="GitHub repo (org/name); auto-detected from git remote if repo_path is given")
    repo_path: str = Field(
        "",
        description=(
            "Absolute local path to the codebase (e.g. D:/Work/MyApp). "
            "When provided: git remote, tech stack, and docs are auto-detected "
            "and ingested into the project KB immediately."
        ),
    )
    project_id: Optional[str] = Field(
        None,
        pattern=r"^[a-z0-9_]{1,40}$",
        description="Custom project_id slug; auto-generated from name if omitted",
    )


class ScanRequest(BaseModel):
    repo_path: str = Field(..., description="Absolute local path to the codebase")


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    description: str
    repo: str
    repo_path: str
    created_at: float
    active: bool
    kb_path: str
    skills_path: str
    scan_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _redis_url() -> str:
    return os.environ["REDIS_URL"]


def _registry() -> ProjectRegistry:
    return ProjectRegistry(_redis_url())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_response(p: Project, scan: ScanResult | None = None) -> ProjectResponse:
    return ProjectResponse(
        project_id=p.project_id,
        name=p.name,
        description=p.description,
        repo=p.repo,
        repo_path=p.repo_path,
        created_at=p.created_at,
        active=p.active,
        kb_path=p.kb_path,
        skills_path=p.skills_path,
        scan_summary=scan.summary() if scan else None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new project",
)
async def create_project(
    body: ProjectCreateRequest,
    registry: ProjectRegistry = Depends(_registry),
) -> ProjectResponse:
    """Register a new codebase with ForgeChain.

    Creates a dedicated knowledge base directory and skills directory for
    the project. Workers loading tasks tagged with this project_id will
    retrieve from both the project KB and the shared KB.

    Use the returned project_id in subsequent job and PRD submissions:
        POST /forgechain/jobs       { "project": "my_project", ... }
        POST /forgechain/prd        { "project": "my_project", "prd": "..." }

    With repo_path: the git remote, stack, and docs are auto-detected.
    Ingestion runs synchronously — expect 10–60s for a large repo.
    """
    if body.project_id and await registry.exists(body.project_id):
        raise HTTPException(
            status_code=409,
            detail=f"Project {body.project_id!r} already exists",
        )

    # Run scanner first to extract git remote before project creation
    scan_result: ScanResult | None = None
    effective_repo = body.repo
    effective_repo_path = body.repo_path

    if body.repo_path:
        if not os.path.exists(body.repo_path):
            raise HTTPException(
                status_code=400,
                detail=f"repo_path does not exist: {body.repo_path!r}",
            )

    project = await registry.create(
        name=body.name,
        description=body.description,
        repo=effective_repo,
        repo_path=effective_repo_path,
        project_id=body.project_id,
    )

    if body.repo_path:
        try:
            scanner = RepoScanner(project.project_id)
            loop = asyncio.get_event_loop()
            scan_result = await loop.run_in_executor(None, scanner.scan, body.repo_path)

            # Update repo field from git remote if not manually provided
            if scan_result.detected_git_remote and not body.repo:
                await registry.update_repo(
                    project.project_id,
                    repo=scan_result.detected_git_remote,
                    repo_path=str(scan_result.repo_path),
                )
                project = await registry.get(project.project_id) or project

            logger.info(
                "[registry] Scan complete for %r: %d chunks across %d roles",
                project.project_id,
                scan_result.total_chunks,
                len(scan_result.roles_discovered),
            )
        except Exception as exc:
            logger.exception("[registry] Scan failed for %r", project.project_id)
            scan_result = None
            # Don't fail the registration — project is created, scan is advisory

    return _to_response(project, scan_result)


@router.get(
    "/projects",
    response_model=list[ProjectResponse],
    summary="List all registered projects",
)
async def list_projects(
    active_only: bool = False,
    registry: ProjectRegistry = Depends(_registry),
) -> list[ProjectResponse]:
    """Return all registered projects, newest first."""
    projects = await registry.list_active() if active_only else await registry.list_all()
    return [_to_response(p) for p in projects]


@router.get(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Get project details",
)
async def get_project(
    project_id: str,
    registry: ProjectRegistry = Depends(_registry),
) -> ProjectResponse:
    project = await registry.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return _to_response(project)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove project from registry",
)
async def delete_project(
    project_id: str,
    registry: ProjectRegistry = Depends(_registry),
) -> None:
    """Remove a project from the registry.

    Does NOT delete knowledge base files from disk — the ChromaDB directory
    at kb_path is preserved so data can be recovered or reassigned.
    """
    if project_id == "shared":
        raise HTTPException(status_code=400, detail="Cannot delete the shared KB project")
    removed = await registry.delete(project_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")


@router.post(
    "/projects/{project_id}/scan",
    summary="Scan a local repo directory and ingest its docs into the project KB",
)
async def scan_project(
    project_id: str,
    body: ScanRequest,
    registry: ProjectRegistry = Depends(_registry),
) -> dict:
    """Point ForgeChain at a local repo folder and let it auto-discover everything.

    The scanner will:
      1. Detect git remote → update the project's repo field.
      2. Detect tech stack (Node, Python, Go, Rust, Java, …).
      3. Map stack → relevant agent roles.
      4. Build a directory-structure summary document.
      5. Ingest README, docs, config summaries, and schema files
         per role into the project KB.

    Example:
        POST /forgechain/projects/boli/scan
        { "repo_path": "D:/Work/SingularRarityLabs/Product/Boli" }

    Ingestion is synchronous — allow 10–60s for a large repo.
    Re-scanning is idempotent: existing chunks are upserted, not duplicated.
    """
    project = await registry.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")

    if not os.path.exists(body.repo_path):
        raise HTTPException(
            status_code=400,
            detail=f"repo_path does not exist: {body.repo_path!r}",
        )

    scanner = RepoScanner(project_id)
    loop = asyncio.get_event_loop()
    result: ScanResult = await loop.run_in_executor(None, scanner.scan, body.repo_path)

    # Persist git remote + repo_path if we learned them
    if result.detected_git_remote or body.repo_path:
        await registry.update_repo(
            project_id,
            repo=result.detected_git_remote or project.repo,
            repo_path=str(result.repo_path),
        )

    return {
        "project_id":           project_id,
        "repo_path":            result.repo_path,
        "detected_git_remote":  result.detected_git_remote,
        "detected_stack":       result.detected_stack,
        "roles_discovered":     result.roles_discovered,
        "chunks_added":         result.chunks_added,
        "total_chunks":         result.total_chunks,
        "files_ingested":       result.files_ingested,
        "warnings":             result.warnings,
        "summary":              result.summary(),
    }


@router.get(
    "/projects/{project_id}/gaps",
    summary="Knowledge gaps for a specific project",
)
async def get_project_gaps(
    project_id: str,
    registry: ProjectRegistry = Depends(_registry),
) -> dict:
    """Return coverage gaps for a specific project's knowledge base.

    Combines project-specific gap analysis with a note on which roles
    could benefit from ingesting the shared KB patterns.
    """
    project = await registry.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")

    tracker = CoverageTracker(_redis_url())
    gaps = await tracker.get_gaps(min_queries=3)
    scores = await tracker.get_all_scores()

    return {
        "project_id": project_id,
        "project_name": project.name,
        "kb_path": project.kb_path,
        "coverage_gaps": gaps,
        "all_role_scores": scores,
        "tip": (
            f"Ingest project-specific docs: "
            f"python -m knowledge.cli ingest file --role <role> "
            f"--project {project_id} <path>"
        ),
    }
