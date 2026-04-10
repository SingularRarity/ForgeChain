"""Dependency Analyzer — LLM-based extraction of third-party dependencies from a PRD + repo scan.

Uses the Senior tier provider (same as backend_dev worker) for structured extraction.
Returns a DependencyAnalysis with every third-party SaaS or proprietary service found,
along with open-source alternatives and a keep/replace/remove recommendation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from providers import get_provider
from .models import ThirdPartyDependency, DependencyAnalysis

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a senior software architect performing a vendor dependency audit.

Given a PRD (and optional repo scan summary), identify every third-party SaaS service,
proprietary API, or commercial SDK the project currently depends on.

For each dependency output a JSON object. Output ONLY a valid JSON object — no markdown,
no explanation, no extra text. Schema:

{
  "dependencies": [
    {
      "name": "Razorpay",
      "category": "payment_gateway",
      "usage_summary": "Handles INR payment collection and webhook callbacks.",
      "removal_risk": "medium",
      "open_source_alternative": "Hyperswitch (juspay/hyperswitch)",
      "alternative_license": "AGPL-3.0",
      "alternative_notes": "Self-hosted, supports UPI/cards/netbanking. AGPL means modifications must be open-sourced."
    }
  ],
  "keep": [],
  "replace": ["Razorpay"],
  "remove": ["Castler"],
  "analysis_notes": "Two third-party services detected. Castler has no viable open-source equivalent and should be replaced by on-chain escrow. Razorpay should be replaced by self-hosted Hyperswitch."
}

Categories: payment_gateway, escrow, auth, crm, email, sms, storage, cdn, analytics,
            monitoring, search, ai_api, database_saas, infra_saas, other

Removal risk: low = drop-in replacement exists; medium = some rework needed; high = architectural change

Rules:
- Only flag EXTERNAL third-party services (not open-source self-hosted tools like PostgreSQL, Redis)
- Prefer Indian open-source alternatives where they exist (Hyperswitch, Digio, etc.)
- If no open-source alternative exists, say so explicitly in alternative_notes
- "keep" = dependency is already open-source/self-hosted or has no viable replacement
- "replace" = dependency should be swapped for the recommended alternative
- "remove" = dependency can be eliminated entirely (e.g., replaced by on-chain logic)
"""


class DependencyAnalyzer:
    """Analyses a PRD + repo scan for third-party dependencies using an LLM."""

    def __init__(self) -> None:
        self._provider = get_provider()

    def analyze(self, prd_text: str, repo_scan_summary: str = "") -> DependencyAnalysis:
        """Run dependency analysis.

        Args:
            prd_text: Raw PRD markdown text.
            repo_scan_summary: Optional repo scan output (from RepoScanner.summary()).

        Returns:
            DependencyAnalysis with all detected dependencies and recommendations.
        """
        user_content = f"## PRD\n\n{prd_text}"
        if repo_scan_summary:
            user_content += f"\n\n## Repo Scan Summary\n\n{repo_scan_summary}"

        logger.info("[onboarding.analyzer] Running dependency analysis (%d chars)", len(user_content))

        raw = asyncio.run(self._provider.complete(
            system=_SYSTEM_PROMPT,
            user=user_content,
            max_tokens=2048,
        )).content

        return self._parse(raw)

    def _parse(self, raw: str) -> DependencyAnalysis:
        # Strip markdown fences if present
        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)

        try:
            data = json.loads(clean)
        except json.JSONDecodeError as exc:
            logger.warning("[onboarding.analyzer] JSON parse failed: %s — returning empty analysis", exc)
            return DependencyAnalysis(
                dependencies=(),
                keep=(),
                replace=(),
                remove=(),
                analysis_notes=f"Parse error: {exc}. Raw output: {raw[:300]}",
            )

        deps = tuple(
            ThirdPartyDependency(
                name=d.get("name", "unknown"),
                category=d.get("category", "other"),
                usage_summary=d.get("usage_summary", ""),
                removal_risk=d.get("removal_risk", "medium"),
                open_source_alternative=d.get("open_source_alternative", ""),
                alternative_license=d.get("alternative_license", ""),
                alternative_notes=d.get("alternative_notes", ""),
            )
            for d in data.get("dependencies", [])
        )

        return DependencyAnalysis(
            dependencies=deps,
            keep=tuple(data.get("keep", [])),
            replace=tuple(data.get("replace", [])),
            remove=tuple(data.get("remove", [])),
            analysis_notes=data.get("analysis_notes", ""),
        )
