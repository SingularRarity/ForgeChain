"""Project Onboarding API routes.

POST /forgechain/onboard
    Full pipeline: analyze PRD → rewrite PRD → generate scripts → register project.
    Returns the rewritten PRD, generated scripts, and execution plan.
    Long-running (~2-5 min depending on LLM tier); runs synchronously in a thread pool.

GET  /forgechain/onboard/{project_id}
    Re-run analysis only (no registration) to refresh dependency report.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from onboarding import (
    OnboardingOrchestrator,
    OnboardingRequest,
    OnboardingResult,
    DependencyAnalyzer,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forgechain", tags=["onboarding"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class OnboardRequest(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=80)
    project_id: str = Field(
        ...,
        pattern=r"^[a-z0-9_]{1,40}$",
        description="Slug used throughout ForgeChain (lowercase, underscores only)",
    )
    repo_path: str = Field(
        ...,
        description="Absolute path to the local repo (accessible from the ForgeChain container)",
    )
    github_repo: str = Field(..., description="org/repo on GitHub")
    github_token: Optional[str] = Field(
        None,
        description="Fine-grained PAT (repo scope). Write-only — never returned.",
    )
    existing_prd: Optional[str] = Field(
        None,
        description="Raw PRD text. If omitted, only repo scan + registration run (no rewrite).",
    )
    description: str = Field("", max_length=500)
    target_testnet_date: str = Field(
        "",
        description="ISO date for testnet go-live, e.g. 2026-04-19. Used in sprint plan generation.",
    )
    platform: str = Field(
        "windows",
        description="Host OS for generated scripts: windows | linux | mac",
    )


class ThirdPartyDepResponse(BaseModel):
    name: str
    category: str
    usage_summary: str
    removal_risk: str
    open_source_alternative: str
    alternative_license: str
    alternative_notes: str


class DependencyAnalysisResponse(BaseModel):
    dependencies: list[ThirdPartyDepResponse]
    keep: list[str]
    replace: list[str]
    remove: list[str]
    analysis_notes: str


class OnboardResponse(BaseModel):
    project_id: str
    project_name: str
    status: str
    prd_version: str
    dependency_analysis: Optional[DependencyAnalysisResponse]
    rewritten_prd: str
    wave_summary: str
    # Generated artifact contents (save these to disk in your project's forgechain/ dir)
    artifacts: dict[str, str] = Field(
        description="Keys: register_ps1, submit_prd_ps1, wave_review_ps1, execution_plan_md"
    )
    error: str


class AnalyzeOnlyRequest(BaseModel):
    prd: str = Field(..., description="Raw PRD text to analyze for third-party dependencies")
    repo_scan_summary: str = Field("", description="Optional repo scan output to augment analysis")


# ── Dependencies ──────────────────────────────────────────────────────────────

def _redis_url() -> str:
    return os.environ["REDIS_URL"]

def _api_url() -> str:
    return os.environ.get("FORGECHAIN_API_URL", "http://localhost:8000")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/onboard",
    response_model=OnboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Onboard a new project — full pipeline",
)
async def onboard_project(
    body: OnboardRequest,
    redis_url: str = Depends(_redis_url),
    api_url: str = Depends(_api_url),
) -> OnboardResponse:
    """Run the full onboarding pipeline for a new project.

    What this does (in order):
    1. Scans the repo at `repo_path` and ingests it into the project knowledge base
    2. Analyses `existing_prd` for third-party SaaS dependencies (LLM, Senior tier)
    3. Rewrites the PRD for an in-house build + ForgeChain orchestration (LLM, CTO tier)
    4. Generates project-specific PowerShell scripts and a day-by-day execution plan
    5. Registers the project in ForgeChain's project registry

    Returns all generated artifacts as strings in `artifacts`. Save them to your project's
    `forgechain/` directory before running `submit_prd.ps1`.

    Allow 2–5 minutes for LLM calls. Progress is logged to the ForgeChain API container.

    Example — onboard DexVault:
        POST /forgechain/onboard
        {
          "project_name": "DexVault",
          "project_id": "dexvault",
          "repo_path": "D:/Work/SingularRarityLabs/Product/DexVault",
          "github_repo": "singularraritylabs/dexvault",
          "github_token": "ghp_...",
          "existing_prd": "<contents of PRDv4.1.md>",
          "target_testnet_date": "2026-04-19",
          "platform": "windows"
        }
    """
    repo_accessible = os.path.exists(body.repo_path)
    if not repo_accessible:
        logger.warning(
            "[onboarding] repo_path %r not accessible from container — proceeding without repo scan",
            body.repo_path,
        )

    request = OnboardingRequest(
        project_name=body.project_name,
        project_id=body.project_id,
        repo_path=body.repo_path,
        github_repo=body.github_repo,
        github_token=body.github_token or "",
        existing_prd=body.existing_prd or "",
        description=body.description,
        target_testnet_date=body.target_testnet_date,
        platform=body.platform,  # type: ignore[arg-type]
    )

    orchestrator = OnboardingOrchestrator(redis_url=redis_url, api_url=api_url)
    result: OnboardingResult = await orchestrator.onboard(request)

    dep_resp: Optional[DependencyAnalysisResponse] = None
    if result.dependency_analysis is not None:
        da = result.dependency_analysis
        dep_resp = DependencyAnalysisResponse(
            dependencies=[
                ThirdPartyDepResponse(
                    name=d.name,
                    category=d.category,
                    usage_summary=d.usage_summary,
                    removal_risk=d.removal_risk,
                    open_source_alternative=d.open_source_alternative,
                    alternative_license=d.alternative_license,
                    alternative_notes=d.alternative_notes,
                )
                for d in da.dependencies
            ],
            keep=list(da.keep),
            replace=list(da.replace),
            remove=list(da.remove),
            analysis_notes=da.analysis_notes,
        )

    return OnboardResponse(
        project_id=result.project_id,
        project_name=result.project_name,
        status=result.status,
        prd_version=result.prd_version,
        dependency_analysis=dep_resp,
        rewritten_prd=result.rewritten_prd,
        wave_summary=result.wave_summary,
        artifacts={
            "register_ps1":     result.register_script,
            "submit_prd_ps1":   result.submit_prd_script,
            "wave_review_ps1":  result.wave_review_script,
            "execution_plan_md": result.execution_plan,
        },
        error=result.error,
    )


@router.post(
    "/onboard/analyze",
    response_model=DependencyAnalysisResponse,
    summary="Analyse a PRD for third-party dependencies only (no registration, no rewrite)",
)
async def analyze_prd(body: AnalyzeOnlyRequest) -> DependencyAnalysisResponse:
    """Run the dependency analysis step only.

    Useful for reviewing what ForgeChain would recommend replacing before
    committing to a full onboarding run. Fast — single LLM call, Senior tier.

    Returns a ranked list of detected third-party dependencies with
    open-source alternatives and a keep/replace/remove recommendation.
    """
    analyzer = DependencyAnalyzer()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        analyzer.analyze,
        body.prd,
        body.repo_scan_summary,
    )

    return DependencyAnalysisResponse(
        dependencies=[
            ThirdPartyDepResponse(
                name=d.name,
                category=d.category,
                usage_summary=d.usage_summary,
                removal_risk=d.removal_risk,
                open_source_alternative=d.open_source_alternative,
                alternative_license=d.alternative_license,
                alternative_notes=d.alternative_notes,
            )
            for d in result.dependencies
        ],
        keep=list(result.keep),
        replace=list(result.replace),
        remove=list(result.remove),
        analysis_notes=result.analysis_notes,
    )
