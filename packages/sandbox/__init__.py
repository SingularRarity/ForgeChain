"""ForgeChain sandbox — validates generated patches against the target project.

Spins up the target project's own Docker environment (not ForgeChain's),
applies the patch, runs the project's own tests, A/B compares API endpoints,
and Playwright-smokes the frontend. All before the job goes to human REVIEW.

Enable via: FORGECHAIN_SANDBOX_ENABLED=1
"""

from .runner import SandboxRunner
from .report import SandboxReport

__all__ = ["SandboxRunner", "SandboxReport"]
