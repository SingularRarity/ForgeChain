"""PRD Rewriter — LLM rewrites an existing PRD to align with in-house build + ForgeChain orchestration.

Takes the original PRD + dependency analysis and produces a new versioned PRD that:
1. Removes all third-party SaaS dependencies flagged for replace/remove
2. Adds in-house alternatives (self-hosted open-source or on-chain solutions)
3. Adds a §ForgeChain Orchestration section with wave decomposition
4. Adds a §Sprint Plan section calibrated to the target testnet date
5. Increments the version number

Uses the CTO-tier provider (highest reasoning quality) since this is the document
that drives the entire build.
"""

from __future__ import annotations

import asyncio
import logging
import re

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from providers import get_provider
from .models import DependencyAnalysis, OnboardingRequest

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a senior product architect rewriting a PRD for an in-house build.

You will receive:
1. The original PRD
2. A dependency analysis listing what to keep, replace, or remove
3. Project metadata (name, github repo, target date, platform)

Your task: produce a fully rewritten PRD that:
- Removes every third-party dependency marked for "replace" or "remove"
- Adds the recommended open-source self-hosted alternatives in their place
- Adds a "§ForgeChain Orchestration" section that defines:
    - POST /forgechain/projects registration call
    - Wave decomposition table (Wave 0..N, role(s), tasks, dependencies)
    - Per-role tier routing (Junior/Mid/Senior)
    - Human review gate description per wave
- Adds or rewrites "§Sprint Plan" calibrated to the target testnet date
    (Day 1 = today; Day N = testnet go-live; use wave-based milestones)
- Increments the PRD version (e.g. v4.1 → v5.0)
- Adds a revision log at the top summarising every change made
- Adds an "§Appendix: ForgeChain Setup Commands" section with PowerShell commands
    (not bash — the platform is Windows with Docker)
- Preserves all sections that did not change (revenue model, legal, KPIs, etc.)

Output the FULL rewritten PRD text only — no explanation, no prefix, no suffix.
Use GitHub-flavored Markdown. Keep the same section structure (§1, §2, ...) and
add new sections at the end before appendices.
"""


class PRDRewriter:
    """Rewrites an existing PRD for an in-house ForgeChain-orchestrated build."""

    def __init__(self) -> None:
        self._provider = get_provider()

    def rewrite(
        self,
        request: OnboardingRequest,
        analysis: DependencyAnalysis,
    ) -> tuple[str, str]:
        """Rewrite the PRD.

        Returns:
            (rewritten_prd_text, prd_version) — version extracted from the rewritten text.
        """
        dep_summary = self._format_analysis(analysis)

        user_content = (
            f"## Project Metadata\n"
            f"- Name: {request.project_name}\n"
            f"- project_id: {request.project_id}\n"
            f"- GitHub: {request.github_repo}\n"
            f"- Target testnet date: {request.target_testnet_date or 'TBD'}\n"
            f"- Platform: {request.platform}\n"
            f"\n## Dependency Analysis\n{dep_summary}"
            f"\n\n## Original PRD\n\n{request.existing_prd}"
        )

        logger.info(
            "[onboarding.prd_rewriter] Rewriting PRD for %r (%d chars in, %d dep(s))",
            request.project_id, len(request.existing_prd), len(analysis.dependencies),
        )

        rewritten = asyncio.run(self._provider.complete(
            system=_SYSTEM_PROMPT,
            user=user_content,
            max_tokens=8192,
        )).content

        version = self._extract_version(rewritten)
        return rewritten, version

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_analysis(analysis: DependencyAnalysis) -> str:
        lines = [f"**Notes:** {analysis.analysis_notes}", ""]
        if analysis.replace:
            lines.append(f"**Replace:** {', '.join(analysis.replace)}")
        if analysis.remove:
            lines.append(f"**Remove:** {', '.join(analysis.remove)}")
        if analysis.keep:
            lines.append(f"**Keep:** {', '.join(analysis.keep)}")
        lines.append("")
        for dep in analysis.dependencies:
            action = (
                "REMOVE" if dep.name in analysis.remove else
                "REPLACE" if dep.name in analysis.replace else
                "KEEP"
            )
            lines.append(f"### [{action}] {dep.name} ({dep.category})")
            lines.append(f"- Usage: {dep.usage_summary}")
            lines.append(f"- Risk: {dep.removal_risk}")
            if dep.open_source_alternative:
                lines.append(f"- Alternative: {dep.open_source_alternative} ({dep.alternative_license})")
                lines.append(f"- Notes: {dep.alternative_notes}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _extract_version(text: str) -> str:
        """Pull the version string from the rewritten PRD header."""
        match = re.search(r"\*\*Version:\*\*\s*([\w.]+)", text)
        if match:
            return match.group(1)
        match = re.search(r"PRD\s+(v[\d.]+)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return "v_next"
