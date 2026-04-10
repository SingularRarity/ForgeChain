"""ForgeChain Project Onboarding Package.

Automates the analysis, PRD rewrite, script generation, and project registration
for any new project being onboarded into ForgeChain.

Pipeline:
    OnboardingRequest → OnboardingOrchestrator → OnboardingResult

    1. RepoScanner  — scans local repo, ingests KB
    2. DependencyAnalyzer  — LLM extracts third-party deps + recommends replacements
    3. PRDRewriter  — LLM rewrites PRD for in-house build + ForgeChain orchestration
    4. ScriptGenerator  — generates PS1 scripts + Markdown execution plan
    5. ProjectRegistry  — registers project in ForgeChain
"""

from .models import (
    OnboardingRequest,
    OnboardingResult,
    DependencyAnalysis,
    ThirdPartyDependency,
    OnboardingStatus,
)
from .analyzer import DependencyAnalyzer
from .prd_rewriter import PRDRewriter
from .script_generator import ScriptGenerator
from .orchestrator import OnboardingOrchestrator

__all__ = [
    "OnboardingRequest",
    "OnboardingResult",
    "DependencyAnalysis",
    "ThirdPartyDependency",
    "OnboardingStatus",
    "DependencyAnalyzer",
    "PRDRewriter",
    "ScriptGenerator",
    "OnboardingOrchestrator",
]
