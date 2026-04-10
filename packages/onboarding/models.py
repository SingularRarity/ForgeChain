"""Onboarding data models — all frozen dataclasses (immutable)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

OnboardingStatus = Literal["pending", "analyzing", "rewriting", "registering", "done", "failed"]


@dataclass(frozen=True)
class ThirdPartyDependency:
    """A detected third-party SaaS or proprietary dependency in a project or PRD."""

    name: str                          # e.g. "Razorpay", "Castler", "Stripe"
    category: str                      # e.g. "payment_gateway", "escrow", "crm", "auth"
    usage_summary: str                 # 1-2 sentences on how it is used
    removal_risk: Literal["low", "medium", "high"]
    open_source_alternative: str       # recommended replacement, "" if none found
    alternative_license: str           # AGPL-3.0, Apache-2.0, MIT, etc.
    alternative_notes: str             # why this alternative, key caveats


@dataclass(frozen=True)
class OnboardingRequest:
    """Input to the onboarding pipeline — everything needed to analyse and onboard a project."""

    project_name: str
    project_id: str                    # slug: lowercase, underscores only
    repo_path: str                     # absolute path to local repo
    github_repo: str                   # "org/repo"
    github_token: str                  # PAT — never returned by API
    existing_prd: str = ""             # raw PRD text if available; "" to skip analysis
    description: str = ""
    target_testnet_date: str = ""      # ISO date string, e.g. "2026-04-19"
    platform: Literal["windows", "linux", "mac"] = "windows"


@dataclass(frozen=True)
class DependencyAnalysis:
    """Result of the LLM dependency analysis step."""

    dependencies: tuple[ThirdPartyDependency, ...]
    keep: tuple[str, ...]              # dependency names to keep as-is
    replace: tuple[str, ...]           # dependency names to replace
    remove: tuple[str, ...]            # dependency names to remove entirely
    analysis_notes: str                # free-text architect summary


@dataclass(frozen=True)
class OnboardingResult:
    """Final output of the full onboarding pipeline."""

    project_id: str
    project_name: str
    status: OnboardingStatus
    # Step outputs
    dependency_analysis: DependencyAnalysis | None
    rewritten_prd: str                 # full PRD v_next text, ready to save
    prd_version: str                   # e.g. "v5.0"
    wave_summary: str                  # human-readable wave plan extracted from rewritten PRD
    # Generated artifacts (file contents — caller decides where to write them)
    register_script: str               # PowerShell register script body
    submit_prd_script: str             # PowerShell submit_prd script body
    wave_review_script: str            # PowerShell wave_review script body
    execution_plan: str                # Markdown execution plan body
    # Metadata
    created_at: float = field(default_factory=time.time)
    error: str = ""                    # non-empty on failure
