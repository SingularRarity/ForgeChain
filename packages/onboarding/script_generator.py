"""Script Generator — produces project-specific PowerShell onboarding scripts.

Takes the OnboardingRequest and the rewritten PRD path and generates:
  - register.ps1      (project registration)
  - submit_prd.ps1    (PRD submission)
  - wave_review.ps1   (wave status checker)
  - EXECUTION_PLAN.md (day-by-day sprint plan extracted from PRD)

These are returned as strings — the caller writes them to disk.
The plan uses an LLM call (mid tier) to extract the wave plan from the rewritten PRD
and format it into a platform-specific Markdown execution plan.
"""

from __future__ import annotations

import asyncio
import logging

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from providers import get_provider
from .models import OnboardingRequest

logger = logging.getLogger(__name__)

_PLAN_SYSTEM = """\
You are a technical project manager. Given a rewritten PRD that contains a ForgeChain
orchestration section and a sprint plan, extract and format a day-by-day EXECUTION_PLAN.md.

The plan must:
- List every day from Day 1 to the testnet go-live day
- Show which ForgeChain waves are dispatched and reviewed each day
- Include PowerShell commands (not bash) for Docker-based validation steps
- Include a "ForgeChain Performance Test Metrics" table at the end
- Note: all services run in Docker; host is Windows; commands use Invoke-RestMethod and docker compose

Output ONLY the Markdown content — no explanation, no wrapping.
"""


class ScriptGenerator:
    """Generates project-specific PowerShell scripts and execution plan."""

    def __init__(self) -> None:
        self._provider = get_provider()

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(
        self,
        request: OnboardingRequest,
        rewritten_prd: str,
        api_url: str = "http://localhost:8000",
    ) -> dict[str, str]:
        """Generate all onboarding artifacts.

        Returns:
            Dict with keys: register_ps1, submit_prd_ps1, wave_review_ps1, execution_plan_md
        """
        return {
            "register_ps1":     self._register(request, api_url),
            "submit_prd_ps1":   self._submit_prd(request, api_url),
            "wave_review_ps1":  self._wave_review(request, api_url),
            "execution_plan_md": self._execution_plan(request, rewritten_prd),
        }

    # ── Script templates ───────────────────────────────────────────────────────

    @staticmethod
    def _register(req: OnboardingRequest, api_url: str) -> str:
        return f"""\
# {req.project_name} — ForgeChain Project Registration
# Run ONCE after ForgeChain is up (docker compose up -d in ForgeChain repo)
#
# Prerequisites:
#   - ForgeChain running in Docker, API exposed at {api_url}
#   - Set $env:FC_GITHUB_PAT before running
#
# Usage:
#   $env:FC_GITHUB_PAT = "ghp_..."
#   .\\forgechain\\register.ps1

$ErrorActionPreference = "Stop"

$ApiUrl  = $env:FORGECHAIN_API_URL ?? "{api_url}"
$RepoPath = $env:FC_REPO_PATH ?? "{req.repo_path}"
$GhRepo  = "{req.github_repo}"
$GhPat   = $env:FC_GITHUB_PAT ?? ""

Write-Host "==> Registering {req.project_name} with ForgeChain at $ApiUrl"

# 1. Health check
Write-Host "[1/4] Checking ForgeChain health..."
$health = Invoke-RestMethod -Uri "$ApiUrl/health" -Method Get
Write-Host "      status: $($health.status)"

# 2. Register project
Write-Host "[2/4] Registering project (scans repo + ingests KB — allow 20-60s)..."
$body = @{{
    name         = "{req.project_name}"
    project_id   = "{req.project_id}"
    description  = "{req.description}"
    repo_path    = $RepoPath
    github_repo  = $GhRepo
    github_token = $GhPat
}} | ConvertTo-Json

$project = Invoke-RestMethod -Uri "$ApiUrl/forgechain/projects" -Method Post `
    -ContentType "application/json" -Body $body

Write-Host "      project_id : $($project.project_id)"
Write-Host "      kb_path    : $($project.kb_path)"
Write-Host "      active     : $($project.active)"

# 3. Verify
Write-Host "[3/4] Verifying registration..."
$check = Invoke-RestMethod -Uri "$ApiUrl/forgechain/projects/{req.project_id}" -Method Get
Write-Host "      repo   : $($check.repo)"
Write-Host "      active : $($check.active)"

# 4. Knowledge gaps
Write-Host "[4/4] Checking knowledge base gaps..."
$gaps = Invoke-RestMethod -Uri "$ApiUrl/forgechain/projects/{req.project_id}/gaps" -Method Get
if ($gaps.coverage_gaps.Count -gt 0) {{
    Write-Host "      Gaps: $($gaps.coverage_gaps -join ', ')"
}} else {{
    Write-Host "      No significant gaps."
}}

Write-Host ""
Write-Host "==> Done. Next: .\\forgechain\\submit_prd.ps1"
"""

    @staticmethod
    def _submit_prd(req: OnboardingRequest, api_url: str) -> str:
        return f"""\
# {req.project_name} — Submit rewritten PRD to ForgeChain for wave-based execution
# Run AFTER register.ps1 completes.
#
# Usage:
#   .\\forgechain\\submit_prd.ps1

$ErrorActionPreference = "Stop"

$ApiUrl  = $env:FORGECHAIN_API_URL ?? "{api_url}"
$PrdFile = Join-Path $PSScriptRoot "..\\local_docs\\product\\PRD-InHouse-ForgeChain.md"

if (-not (Test-Path $PrdFile)) {{
    Write-Error "PRD not found at: $PrdFile"
    exit 1
}}

$PrdContent = Get-Content -Path $PrdFile -Raw

Write-Host "==> Submitting {req.project_name} PRD to ForgeChain"
Write-Host "    API     : $ApiUrl"
Write-Host "    Project : {req.project_id}"
Write-Host ""
$confirm = Read-Host "Submit now? [y/N]"
if ($confirm -notmatch "^[Yy]$") {{ Write-Host "Aborted."; exit 0 }}

$body = @{{
    project = "{req.project_id}"
    prd     = $PrdContent
}} | ConvertTo-Json -Depth 3

Write-Host "Submitting..."
$response = Invoke-RestMethod -Uri "$ApiUrl/forgechain/prd" -Method Post `
    -ContentType "application/json" -Body $body -TimeoutSec 120

$response | ConvertTo-Json | Write-Host

Write-Host ""
Write-Host "==> Monitor: https://github.com/{req.github_repo}/pulls"
Write-Host "    Jobs   : GET $ApiUrl/forgechain/jobs?project={req.project_id}"
"""

    @staticmethod
    def _wave_review(req: OnboardingRequest, api_url: str) -> str:
        return f"""\
# {req.project_name} — ForgeChain wave status and open PRs
#
# Usage:
#   .\\forgechain\\wave_review.ps1

$ErrorActionPreference = "Stop"

$ApiUrl = $env:FORGECHAIN_API_URL ?? "{api_url}"
$GhRepo = "{req.github_repo}"

Write-Host "==> {req.project_name} ForgeChain Wave Status"
Write-Host ""

Write-Host "[Jobs]"
try {{
    $jobs = Invoke-RestMethod -Uri "$ApiUrl/forgechain/jobs?project={req.project_id}" -Method Get
    if ($jobs.Count -eq 0) {{
        Write-Host "  No active jobs."
    }} else {{
        foreach ($j in $jobs) {{
            $desc = if ($j.description.Length -gt 60) {{ $j.description.Substring(0,60) + "..." }} else {{ $j.description }}
            Write-Host ("  [{{0,10}}]  {{1}}  role={{2}}  {{3}}" -f $j.state, $j.task_id.Substring(0,12), $j.role, $desc)
        }}
    }}
}} catch {{
    Write-Host "  Could not reach ForgeChain API at $ApiUrl"
}}

Write-Host ""
Write-Host "[PRs]"
if (Get-Command gh -ErrorAction SilentlyContinue) {{
    gh pr list --repo $GhRepo --state open
}} else {{
    Write-Host "  https://github.com/$GhRepo/pulls"
}}

Write-Host ""
Write-Host "Approve: Invoke-RestMethod -Uri '$ApiUrl/forgechain/jobs/<id>/approve' -Method Post"
Write-Host "Reject : Invoke-RestMethod -Uri '$ApiUrl/forgechain/jobs/<id>/reject' -Method Post -Body (@{{reason='...'}}|ConvertTo-Json)"
"""

    def _execution_plan(self, req: OnboardingRequest, rewritten_prd: str) -> str:
        """Use LLM (mid tier) to extract wave plan from PRD and format as Markdown."""
        user = (
            f"Project: {req.project_name} ({req.project_id})\n"
            f"GitHub: {req.github_repo}\n"
            f"Target testnet: {req.target_testnet_date or 'TBD'}\n"
            f"Platform: {req.platform} + Docker\n"
            f"ForgeChain API: http://localhost:8000\n\n"
            f"## Rewritten PRD (extract wave plan from this):\n\n{rewritten_prd}"
        )

        logger.info("[onboarding.script_generator] Generating execution plan for %r", req.project_id)

        return asyncio.run(self._provider.complete(
            system=_PLAN_SYSTEM,
            user=user,
            max_tokens=4096,
        )).content
