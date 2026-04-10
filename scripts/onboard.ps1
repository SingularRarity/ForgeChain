# ForgeChain — Universal Project Onboarding Script
# Runs the full onboarding pipeline against the ForgeChain API (Docker-hosted).
#
# What it does:
#   1. Calls POST /forgechain/onboard with your project details and existing PRD
#   2. ForgeChain analyses the PRD for third-party dependencies
#   3. Rewrites the PRD for an in-house build with ForgeChain orchestration
#   4. Generates project-specific PowerShell scripts and a day-by-day execution plan
#   5. Registers the project in ForgeChain
#   6. Saves all generated artifacts to <repo_path>/forgechain/
#
# Prerequisites:
#   - ForgeChain running: docker compose up -d  (in ForgeChain repo)
#   - $env:FC_GITHUB_PAT set (fine-grained PAT, repo scope only)
#
# Usage:
#   $env:FC_GITHUB_PAT = "ghp_..."
#
#   # Minimal (no existing PRD — repo scan + registration only):
#   .\scripts\onboard.ps1 -ProjectName "MyApp" -ProjectId "myapp" `
#       -RepoPath "D:\Work\MyOrg\MyApp" -GithubRepo "myorg/myapp"
#
#   # Full pipeline (with existing PRD — recommended):
#   .\scripts\onboard.ps1 -ProjectName "MyApp" -ProjectId "myapp" `
#       -RepoPath "D:\Work\MyOrg\MyApp" -GithubRepo "myorg/myapp" `
#       -PrdPath "D:\Work\MyOrg\MyApp\docs\PRD.md" `
#       -TargetDate "2026-05-01"

param(
    [Parameter(Mandatory)]
    [string]$ProjectName,

    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z0-9_]{1,40}$')]
    [string]$ProjectId,

    [Parameter(Mandatory)]
    [string]$RepoPath,

    [Parameter(Mandatory)]
    [string]$GithubRepo,

    [string]$PrdPath        = "",
    [string]$TargetDate     = "",
    [string]$Description    = "",
    [string]$ApiUrl         = "http://localhost:8000",
    [string]$Platform       = "windows",
    [switch]$AnalyzeOnly    # Run dependency analysis only (no rewrite, no registration)
)

$ErrorActionPreference = "Stop"

# ── Validation ─────────────────────────────────────────────────────────────────

$GhPat = $env:FC_GITHUB_PAT
if (-not $GhPat -and -not $AnalyzeOnly) {
    Write-Error "Set `$env:FC_GITHUB_PAT before running. Example: `$env:FC_GITHUB_PAT = 'ghp_...'"
    exit 1
}

if (-not (Test-Path $RepoPath)) {
    Write-Error "RepoPath not found: $RepoPath"
    exit 1
}

$PrdContent = ""
if ($PrdPath) {
    if (-not (Test-Path $PrdPath)) {
        Write-Error "PrdPath not found: $PrdPath"
        exit 1
    }
    $PrdContent = Get-Content -Path $PrdPath -Raw
    Write-Host "  PRD loaded: $PrdPath ($($PrdContent.Length) chars)"
}

# ── Health check ────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "==> ForgeChain Project Onboarding"
Write-Host "    Project : $ProjectName ($ProjectId)"
Write-Host "    Repo    : $RepoPath"
Write-Host "    GitHub  : $GithubRepo"
Write-Host "    API     : $ApiUrl"
if ($PrdContent) { Write-Host "    PRD     : $PrdPath" }
if ($TargetDate) { Write-Host "    Target  : $TargetDate" }
Write-Host ""

Write-Host "[preflight] Checking ForgeChain health..."
try {
    $health = Invoke-RestMethod -Uri "$ApiUrl/health" -Method Get -TimeoutSec 5
    Write-Host "            status: $($health.status)"
} catch {
    Write-Error "Cannot reach ForgeChain API at $ApiUrl. Is `docker compose up -d` running in the ForgeChain repo?"
    exit 1
}

# ── Analyze only mode ───────────────────────────────────────────────────────────

if ($AnalyzeOnly) {
    if (-not $PrdContent) {
        Write-Error "-AnalyzeOnly requires -PrdPath"
        exit 1
    }
    Write-Host ""
    Write-Host "[analyze] Running dependency analysis (Senior tier LLM)..."
    $body = @{ prd = $PrdContent } | ConvertTo-Json -Depth 3
    $result = Invoke-RestMethod -Uri "$ApiUrl/forgechain/onboard/analyze" -Method Post `
        -ContentType "application/json" -Body $body -TimeoutSec 120

    Write-Host ""
    Write-Host "==> Dependency Analysis Results"
    Write-Host "    Notes  : $($result.analysis_notes)"
    Write-Host "    Replace: $($result.replace -join ', ')"
    Write-Host "    Remove : $($result.remove -join ', ')"
    Write-Host "    Keep   : $($result.keep -join ', ')"
    Write-Host ""
    Write-Host "Dependencies:"
    foreach ($dep in $result.dependencies) {
        Write-Host "  [$($dep.removal_risk.ToUpper())] $($dep.name) ($($dep.category))"
        Write-Host "    Usage      : $($dep.usage_summary)"
        if ($dep.open_source_alternative) {
            Write-Host "    Alternative: $($dep.open_source_alternative) ($($dep.alternative_license))"
            Write-Host "    Notes      : $($dep.alternative_notes)"
        }
        Write-Host ""
    }
    exit 0
}

# ── Full onboarding pipeline ────────────────────────────────────────────────────

$confirm = Read-Host "Run full onboarding pipeline? (analyze + rewrite PRD + generate scripts + register) [y/N]"
if ($confirm -notmatch "^[Yy]$") { Write-Host "Aborted."; exit 0 }

Write-Host ""
Write-Host "[onboard] Submitting to POST /forgechain/onboard..."
Write-Host "          This runs 3 LLM calls (Senior + CTO + Mid tier) — allow 2-5 minutes."
Write-Host ""

$body = @{
    project_name        = $ProjectName
    project_id          = $ProjectId
    repo_path           = $RepoPath
    github_repo         = $GithubRepo
    github_token        = $GhPat
    existing_prd        = $PrdContent
    description         = $Description
    target_testnet_date = $TargetDate
    platform            = $Platform
} | ConvertTo-Json -Depth 3

$result = Invoke-RestMethod -Uri "$ApiUrl/forgechain/onboard" -Method Post `
    -ContentType "application/json" -Body $body -TimeoutSec 600

# ── Results ─────────────────────────────────────────────────────────────────────

Write-Host "==> Onboarding complete"
Write-Host "    status      : $($result.status)"
Write-Host "    prd_version : $($result.prd_version)"
if ($result.error) { Write-Host "    ERROR       : $($result.error)" }

if ($result.dependency_analysis) {
    $da = $result.dependency_analysis
    Write-Host ""
    Write-Host "    Dependencies detected: $($da.dependencies.Count)"
    Write-Host "    Replace: $($da.replace -join ', ')"
    Write-Host "    Remove : $($da.remove -join ', ')"
}

# ── Save artifacts to <repo_path>/forgechain/ ──────────────────────────────────

$OutDir = Join-Path $RepoPath "forgechain"
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}

$artifacts = $result.artifacts
$saved = @()

if ($artifacts.register_ps1) {
    $p = Join-Path $OutDir "register.ps1"
    Set-Content -Path $p -Value $artifacts.register_ps1 -Encoding UTF8
    $saved += "register.ps1"
}
if ($artifacts.submit_prd_ps1) {
    $p = Join-Path $OutDir "submit_prd.ps1"
    Set-Content -Path $p -Value $artifacts.submit_prd_ps1 -Encoding UTF8
    $saved += "submit_prd.ps1"
}
if ($artifacts.wave_review_ps1) {
    $p = Join-Path $OutDir "wave_review.ps1"
    Set-Content -Path $p -Value $artifacts.wave_review_ps1 -Encoding UTF8
    $saved += "wave_review.ps1"
}
if ($artifacts.execution_plan_md) {
    $p = Join-Path $OutDir "EXECUTION_PLAN.md"
    Set-Content -Path $p -Value $artifacts.execution_plan_md -Encoding UTF8
    $saved += "EXECUTION_PLAN.md"
}

# Save rewritten PRD
$PrdOutDir = Join-Path $RepoPath "local_docs\product"
if (-not (Test-Path $PrdOutDir)) {
    New-Item -ItemType Directory -Path $PrdOutDir -Force | Out-Null
}
if ($result.rewritten_prd) {
    $prdFile = Join-Path $PrdOutDir "PRD-InHouse-ForgeChain.md"
    Set-Content -Path $prdFile -Value $result.rewritten_prd -Encoding UTF8
    $saved += "local_docs\product\PRD-InHouse-ForgeChain.md"
}

Write-Host ""
Write-Host "==> Artifacts saved to: $RepoPath"
foreach ($f in $saved) { Write-Host "    $f" }

Write-Host ""
Write-Host "==> Next steps:"
Write-Host "    1. Review the rewritten PRD: $PrdOutDir\PRD-InHouse-ForgeChain.md"
Write-Host "    2. Submit to ForgeChain:     $OutDir\submit_prd.ps1"
Write-Host "    3. Monitor waves:            $OutDir\wave_review.ps1"
