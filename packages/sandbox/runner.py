"""SandboxRunner — validate a ForgeChain patch against the target project.

Full pipeline:
  1. Copy the target project repo twice: baseline + patched
  2. Apply the patch to the patched copy
  3. Run the project's own test suite directly on the patched copy
  4. Spin up both Docker stacks (baseline + patched) in isolation
  5. A/B compare API responses: same endpoints, before vs after
  6. Playwright smoke test on the patched frontend (if present)
  7. Tear down both Docker stacks
  8. Return a SandboxReport attached to the job metadata

Configuration via environment variables:
  FORGECHAIN_SANDBOX_ENABLED=1   (default: 0 — opt-in)
  FORGECHAIN_SANDBOX_SKIP_DOCKER=1  — skip Docker steps (tests only, faster)
  FORGECHAIN_SANDBOX_SKIP_BROWSER=1 — skip Playwright smoke
  FORGECHAIN_SANDBOX_SKIP_AB=1      — skip A/B comparison

The sandbox requires the worker container to mount the Docker socket:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from .patch_applicator import PatchApplicator
from .project_env import ProjectEnvironment
from .test_runner import TestRunner
from .ab_comparator import ABComparator
from .smoke_tester import SmokeTester
from .report import SandboxReport

logger = logging.getLogger(__name__)

_ENABLED         = os.getenv("FORGECHAIN_SANDBOX_ENABLED",      "0") == "1"
_SKIP_DOCKER     = os.getenv("FORGECHAIN_SANDBOX_SKIP_DOCKER",  "0") == "1"
_SKIP_BROWSER    = os.getenv("FORGECHAIN_SANDBOX_SKIP_BROWSER", "0") == "1"
_SKIP_AB         = os.getenv("FORGECHAIN_SANDBOX_SKIP_AB",      "0") == "1"
_SCREENSHOT_DIR  = Path(os.getenv("FORGECHAIN_SANDBOX_SCREENSHOTS", "/app/sandbox_screenshots"))


class SandboxRunner:
    """Orchestrates the full patch validation pipeline."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def validate(
        self,
        task_id: str,
        patch: str,
        repo_path: str,
        project_id: Optional[str] = None,
    ) -> SandboxReport:
        """Run the full sandbox validation. Always returns a report, never raises."""
        if not _ENABLED:
            return SandboxReport.skipped(task_id, "FORGECHAIN_SANDBOX_ENABLED is not set")

        if not repo_path or not Path(repo_path).exists():
            return SandboxReport.skipped(task_id, f"repo_path not accessible: {repo_path!r}")

        report = SandboxReport(
            task_id=task_id,
            project_id=project_id,
            repo_path=repo_path,
            patch_preview=(patch[:500] + "…") if len(patch) > 500 else patch,
            started_at=time.time(),
        )

        sandbox_id = f"fc_{task_id[:8]}_{uuid.uuid4().hex[:6]}"
        applicator = PatchApplicator(repo_path, patch)

        try:
            async with applicator.apply() as (baseline_dir, patched_dir):
                # ── Step 1: Run project's own test suite ─────────────── #
                logger.info("[sandbox:%s] Running test suite on patched copy", sandbox_id)
                runner = TestRunner(patched_dir)
                loop = asyncio.get_event_loop()
                report.test_suite = await loop.run_in_executor(None, runner.run)
                logger.info("[sandbox:%s] Tests: %s", sandbox_id, report.test_suite.summary())

                # ── Steps 2 & 3: Docker A/B + browser smoke ──────────── #
                if not _SKIP_DOCKER:
                    await self._run_docker_steps(
                        report, sandbox_id, baseline_dir, patched_dir
                    )
                else:
                    logger.info("[sandbox:%s] Docker steps skipped (FORGECHAIN_SANDBOX_SKIP_DOCKER)", sandbox_id)

        except Exception as exc:
            logger.exception("[sandbox:%s] Sandbox pipeline failed", sandbox_id)
            report.error = str(exc)

        report.finished_at = time.time()
        logger.info(
            "[sandbox:%s] %s (%.1fs)",
            sandbox_id,
            "PASSED" if report.overall_passed else "FAILED",
            report.duration_s,
        )
        return report

    async def _run_docker_steps(
        self,
        report: SandboxReport,
        sandbox_id: str,
        baseline_dir: Path,
        patched_dir: Path,
    ) -> None:
        """Spin up both Docker stacks in parallel, A/B compare, smoke test."""
        baseline_name = f"{sandbox_id}_baseline"
        patched_name  = f"{sandbox_id}_patched"

        # Start both stacks concurrently
        baseline_env = ProjectEnvironment(baseline_dir, baseline_name)
        patched_env  = ProjectEnvironment(patched_dir,  patched_name)

        try:
            logger.info("[sandbox:%s] Starting Docker stacks ...", sandbox_id)
            await asyncio.gather(
                baseline_env.__aenter__(),
                patched_env.__aenter__(),
            )

            # ── A/B comparison ──────────────────────────────────────── #
            if not _SKIP_AB and baseline_env.api_url and patched_env.api_url:
                logger.info(
                    "[sandbox:%s] A/B: %s vs %s",
                    sandbox_id, baseline_env.api_url, patched_env.api_url,
                )
                comparator = ABComparator(baseline_env.api_url, patched_env.api_url)
                report.ab_results = await comparator.compare_all()
                ab_pass = sum(1 for r in report.ab_results if r.ok)
                logger.info(
                    "[sandbox:%s] A/B: %d/%d passed",
                    sandbox_id, ab_pass, len(report.ab_results),
                )
            else:
                logger.info("[sandbox:%s] A/B skipped (no API URL or SKIP_AB set)", sandbox_id)

            # ── Browser smoke test on patched environment ──────────── #
            if not _SKIP_BROWSER and patched_env.frontend_url:
                logger.info("[sandbox:%s] Browser smoke: %s", sandbox_id, patched_env.frontend_url)
                screenshot_dir = _SCREENSHOT_DIR / sandbox_id
                tester = SmokeTester(patched_env.frontend_url, screenshot_dir)
                report.smoke = await tester.run()
                logger.info("[sandbox:%s] Smoke: %s", sandbox_id, report.smoke.summary())
            else:
                logger.info("[sandbox:%s] Browser smoke skipped (no frontend URL or SKIP_BROWSER)", sandbox_id)

        finally:
            # Tear down both stacks regardless of test outcome
            await asyncio.gather(
                baseline_env.__aexit__(None, None, None),
                patched_env.__aexit__(None, None, None),
                return_exceptions=True,
            )
            logger.info("[sandbox:%s] Docker stacks stopped", sandbox_id)
