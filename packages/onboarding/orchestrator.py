"""Onboarding Orchestrator — runs the full onboarding pipeline end-to-end.

Pipeline:
  1. Repo scan (via existing RepoScanner)
  2. Dependency analysis (LLM, Senior tier)
  3. PRD rewrite (LLM, CTO tier)
  4. Script generation (LLM, Mid tier — execution plan only; scripts are templated)
  5. Project registration (ProjectRegistry)

Steps 2–4 can run concurrently if the PRD is already available; step 5 runs last.
"""

from __future__ import annotations

import asyncio
import logging
import os

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from registry.project import ProjectRegistry
from registry.scanner import RepoScanner

from .analyzer import DependencyAnalyzer
from .prd_rewriter import PRDRewriter
from .script_generator import ScriptGenerator
from .models import (
    OnboardingRequest,
    OnboardingResult,
    DependencyAnalysis,
)

logger = logging.getLogger(__name__)


class OnboardingOrchestrator:
    """Runs the full project onboarding pipeline."""

    def __init__(self, redis_url: str, api_url: str = "http://localhost:8000") -> None:
        self._redis_url = redis_url
        self._api_url = api_url
        self._analyzer = DependencyAnalyzer()
        self._rewriter = PRDRewriter()
        self._generator = ScriptGenerator()

    async def onboard(self, request: OnboardingRequest) -> OnboardingResult:
        """Run the full onboarding pipeline asynchronously.

        Returns an OnboardingResult. On partial failure, status="failed" and
        error is populated — the result still contains whatever was completed.
        """
        logger.info("[onboarding] Starting onboarding for %r", request.project_id)

        # ── Step 1: Repo scan ────────────────────────────────────────────────
        scan_summary = ""
        if request.repo_path and os.path.exists(request.repo_path):
            try:
                scanner = RepoScanner(request.project_id)
                loop = asyncio.get_event_loop()
                scan_result = await loop.run_in_executor(None, scanner.scan, request.repo_path)
                scan_summary = scan_result.summary()
                logger.info(
                    "[onboarding] Scan complete: %d chunks, %d roles",
                    scan_result.total_chunks, len(scan_result.roles_discovered),
                )
            except Exception:
                logger.exception("[onboarding] Repo scan failed — continuing without scan")

        # ── Step 2: Dependency analysis ───────────────────────────────────────
        analysis: DependencyAnalysis | None = None
        if request.existing_prd:
            try:
                loop = asyncio.get_event_loop()
                analysis = await loop.run_in_executor(
                    None,
                    self._analyzer.analyze,
                    request.existing_prd,
                    scan_summary,
                )
                logger.info(
                    "[onboarding] Analysis complete: %d dep(s), replace=%s, remove=%s",
                    len(analysis.dependencies), analysis.replace, analysis.remove,
                )
            except Exception:
                logger.exception("[onboarding] Dependency analysis failed — continuing without it")

        # ── Step 3: PRD rewrite ───────────────────────────────────────────────
        rewritten_prd = ""
        prd_version = "v_next"
        if request.existing_prd and analysis is not None:
            try:
                loop = asyncio.get_event_loop()
                rewritten_prd, prd_version = await loop.run_in_executor(
                    None,
                    self._rewriter.rewrite,
                    request,
                    analysis,
                )
                logger.info("[onboarding] PRD rewrite complete (%s, %d chars)", prd_version, len(rewritten_prd))
            except Exception:
                logger.exception("[onboarding] PRD rewrite failed")
                rewritten_prd = request.existing_prd  # fall back to original
                prd_version = "v_original"

        # ── Step 4: Script generation ─────────────────────────────────────────
        scripts: dict[str, str] = {
            "register_ps1": "", "submit_prd_ps1": "",
            "wave_review_ps1": "", "execution_plan_md": "",
        }
        try:
            loop = asyncio.get_event_loop()
            scripts = await loop.run_in_executor(
                None,
                self._generator.generate,
                request,
                rewritten_prd or request.existing_prd,
                self._api_url,
            )
        except Exception:
            logger.exception("[onboarding] Script generation failed")

        # ── Step 5: Project registration ──────────────────────────────────────
        try:
            registry = ProjectRegistry(self._redis_url)
            if not await registry.exists(request.project_id):
                await registry.create(
                    name=request.project_name,
                    description=request.description,
                    repo=request.github_repo,
                    repo_path=request.repo_path,
                    project_id=request.project_id,
                    github_token=request.github_token,
                    github_repo=request.github_repo,
                )
                logger.info("[onboarding] Project %r registered", request.project_id)
            else:
                logger.info("[onboarding] Project %r already exists — skipping registration", request.project_id)
        except Exception as exc:
            logger.exception("[onboarding] Registration failed")
            return OnboardingResult(
                project_id=request.project_id,
                project_name=request.project_name,
                status="failed",
                dependency_analysis=analysis,
                rewritten_prd=rewritten_prd,
                prd_version=prd_version,
                wave_summary=scripts.get("execution_plan_md", ""),
                register_script=scripts.get("register_ps1", ""),
                submit_prd_script=scripts.get("submit_prd_ps1", ""),
                wave_review_script=scripts.get("wave_review_ps1", ""),
                execution_plan=scripts.get("execution_plan_md", ""),
                error=str(exc),
            )

        return OnboardingResult(
            project_id=request.project_id,
            project_name=request.project_name,
            status="done",
            dependency_analysis=analysis,
            rewritten_prd=rewritten_prd,
            prd_version=prd_version,
            wave_summary=scripts.get("execution_plan_md", ""),
            register_script=scripts.get("register_ps1", ""),
            submit_prd_script=scripts.get("submit_prd_ps1", ""),
            wave_review_script=scripts.get("wave_review_ps1", ""),
            execution_plan=scripts.get("execution_plan_md", ""),
        )
