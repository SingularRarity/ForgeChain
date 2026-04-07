"""Repo Scanner — auto-discovers and ingests a local codebase into a project KB.

Given a local repo path (e.g. D:/Work/MyApp), the scanner:
  1. Detects the git remote → extracts org/repo for PR creation.
  2. Detects the tech stack from manifest files.
  3. Maps stack → agent roles (frontend_dev, backend_dev, db_eng, etc.)
  4. Builds a project-structure summary document for structural context.
  5. Ingests documentation files (README, docs/, config summaries, schemas)
     into the project-specific KB for each relevant role.

Raw source files are NOT ingested — they are too noisy and large.
Instead the scanner captures:
  - Markdown docs (README, ARCHITECTURE, CHANGELOG, docs/**)
  - Config summaries (package.json deps, pyproject.toml, go.mod)
  - Schema files (Prisma schema, SQL migrations, OpenAPI specs)
  - CI/CD configs (.github/workflows, Dockerfile comments)
  - Directory-tree summary (structural context, no source content)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Tech stack detection ──────────────────────────────────────────────────── #

_STACK_MANIFESTS: dict[str, list[str]] = {
    "node":    ["package.json"],
    "python":  ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
    "go":      ["go.mod"],
    "rust":    ["Cargo.toml"],
    "java":    ["pom.xml", "build.gradle", "build.gradle.kts"],
    "dotnet":  ["*.csproj", "*.sln"],
    "ruby":    ["Gemfile"],
    "php":     ["composer.json"],
}

_STACK_TO_ROLES: dict[str, list[str]] = {
    "node":   ["frontend_dev", "backend_dev", "qa_backend"],
    "python": ["backend_dev", "qa_backend"],
    "go":     ["backend_dev", "sre"],
    "rust":   ["backend_dev"],
    "java":   ["backend_dev", "qa_backend"],
    "dotnet": ["backend_dev", "qa_backend"],
    "ruby":   ["backend_dev"],
    "php":    ["backend_dev"],
}

# Extra role detectors based on presence of specific files/dirs
_EXTRA_ROLE_DETECTORS: list[tuple[list[str], str]] = [
    (["prisma/schema.prisma", "migrations/", "alembic.ini", "*.sql"],   "db_eng"),
    (["Dockerfile", "docker-compose.yml", ".github/workflows/"],        "sre"),
    (["pytest.ini", "jest.config.*", "vitest.config.*", "tests/", "spec/"], "qa_backend"),
    (["requirements.txt", "pyproject.toml"],                            "ai_eng"),   # refined below
    (["docs/", "ARCHITECTURE.md", "ADR/"],                              "ba"),
]

# Node framework detection for role refinement
_NODE_FRONTEND_DEPS = {"react", "next", "vue", "nuxt", "svelte", "angular", "vite"}
_NODE_BACKEND_DEPS  = {"express", "fastify", "koa", "nestjs", "@nestjs/core", "hapi"}
_AI_DEPS            = {"torch", "transformers", "tensorflow", "langchain", "openai",
                       "anthropic", "sklearn", "scikit-learn", "pandas", "numpy"}

# Files ingested per role regardless of stack
_ALWAYS_INGEST: list[str] = [
    "README.md", "README.rst", "README.txt",
    "ARCHITECTURE.md", "CONTRIBUTING.md", "SECURITY.md",
    ".env.example",
]

# Glob patterns scanned for docs per role
_ROLE_DOC_PATTERNS: dict[str, list[str]] = {
    "backend_dev":  ["docs/**/*.md", "api/**/*.md", "*.md"],
    "frontend_dev": ["docs/**/*.md", "*.md", "src/**/*.md"],
    "db_eng":       ["prisma/schema.prisma", "migrations/**/*.sql",
                     "alembic/**/*.py", "schema/**/*.sql", "docs/**/*.md"],
    "qa_backend":   ["docs/**/*.md", "*.md"],
    "sre":          [".github/workflows/*.yml", "Dockerfile",
                     "docker-compose*.yml", "docs/**/*.md"],
    "ai_eng":       ["docs/**/*.md", "*.md", "notebooks/**/*.md"],
    "ba":           ["docs/**/*.md", "ADR/**/*.md", "CHANGELOG.md", "*.md"],
}

_MAX_CONFIG_CHARS = 4000    # cap config file snippets


@dataclass
class ScanResult:
    """Summary of a completed repo scan."""

    project_id: str
    repo_path: str
    detected_git_remote: str           # e.g. "org/repo" or ""
    detected_stack: list[str]          # e.g. ["node", "python"]
    roles_discovered: list[str]        # roles that have relevant files
    files_ingested: dict[str, list[str]]  # role → list of ingested paths
    chunks_added: dict[str, int]       # role → count
    skipped: list[str]                 # files found but skipped
    warnings: list[str]

    @property
    def total_chunks(self) -> int:
        return sum(self.chunks_added.values())

    def summary(self) -> str:
        lines = [
            f"Project:  {self.project_id}",
            f"Path:     {self.repo_path}",
            f"Remote:   {self.detected_git_remote or '(none detected)'}",
            f"Stack:    {', '.join(self.detected_stack) or '(unknown)'}",
            f"Roles:    {', '.join(self.roles_discovered) or '(none)'}",
            f"Chunks:   {self.total_chunks} total",
        ]
        for role, files in self.files_ingested.items():
            n = self.chunks_added.get(role, 0)
            lines.append(f"  {role:20s}  {n:>4d} chunks  ({len(files)} files)")
        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ! {w}")
        return "\n".join(lines)


class RepoScanner:
    """Scan a local repo and populate a project's knowledge base."""

    def __init__(self, project_id: str, redis_url: str | None = None) -> None:
        self.project_id = project_id
        self._redis_url = redis_url or os.getenv("REDIS_URL", "")

    def scan(self, repo_path: str) -> ScanResult:
        """Full scan: detect stack → build structure doc → ingest docs.

        Synchronous — runs in a Celery worker or a background thread.
        Returns a ScanResult with everything that happened.
        """
        root = Path(repo_path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Repo path not found: {repo_path}")

        result = ScanResult(
            project_id=self.project_id,
            repo_path=str(root),
            detected_git_remote="",
            detected_stack=[],
            roles_discovered=[],
            files_ingested={},
            chunks_added={},
            skipped=[],
            warnings=[],
        )

        # 1. Git remote
        result.detected_git_remote = _detect_git_remote(root)

        # 2. Stack detection
        stack, pkg_data = _detect_stack(root)
        result.detected_stack = stack

        # 3. Role determination
        roles = _determine_roles(root, stack, pkg_data)
        result.roles_discovered = roles

        # 4. Build + ingest structure summary (all roles)
        structure_doc = _build_structure_doc(root, stack, pkg_data)
        for role in roles:
            n = self._ingest_text(
                structure_doc,
                source=f"repo-structure:{self.project_id}",
                role=role,
            )
            result.chunks_added[role] = result.chunks_added.get(role, 0) + n

        # 5. Always-ingest files
        for filename in _ALWAYS_INGEST:
            path = root / filename
            if path.exists():
                for role in roles:
                    n = self._ingest_file(str(path), role, result)
                    result.chunks_added[role] = result.chunks_added.get(role, 0) + n

        # 6. Role-specific doc patterns
        for role in roles:
            patterns = _ROLE_DOC_PATTERNS.get(role, ["docs/**/*.md", "*.md"])
            for pattern in patterns:
                for match in sorted(root.glob(pattern))[:30]:   # cap per pattern
                    if match.is_file() and _is_ingestible(match):
                        n = self._ingest_file(str(match), role, result)
                        result.chunks_added[role] = result.chunks_added.get(role, 0) + n

        return result

    # ------------------------------------------------------------------

    def _ingest_file(self, path: str, role: str, result: ScanResult) -> int:
        from knowledge.ingester import Ingester
        try:
            ing = Ingester(role=role, project_id=self.project_id)
            n = ing.ingest_file(path)
            result.files_ingested.setdefault(role, [])
            if path not in result.files_ingested[role]:
                result.files_ingested[role].append(path)
            return n
        except Exception as e:
            result.warnings.append(f"{Path(path).name}: {e}")
            return 0

    def _ingest_text(self, text: str, source: str, role: str) -> int:
        from knowledge.ingester import Ingester
        try:
            ing = Ingester(role=role, project_id=self.project_id)
            return ing.ingest_text(text, source=source)
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _detect_git_remote(root: Path) -> str:
    """Run `git remote get-url origin` and parse to org/repo format."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
        # SSH:   git@github.com:org/repo.git  → org/repo
        # HTTPS: https://github.com/org/repo  → org/repo
        m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", out)
        return m.group(1) if m else out
    except Exception:
        return ""


def _detect_stack(root: Path) -> tuple[list[str], dict[str, Any]]:
    """Return (detected_stacks, parsed_package_data)."""
    found: list[str] = []
    pkg_data: dict[str, Any] = {}

    for stack, manifests in _STACK_MANIFESTS.items():
        for manifest in manifests:
            if "*" in manifest:
                matches = list(root.glob(manifest))
                if matches:
                    found.append(stack)
                    break
            elif (root / manifest).exists():
                found.append(stack)
                # Parse package.json for dep analysis
                if manifest == "package.json":
                    pkg_data["node"] = _parse_package_json(root / manifest)
                elif manifest == "pyproject.toml":
                    pkg_data["python"] = _parse_pyproject(root / manifest)
                elif manifest == "go.mod":
                    pkg_data["go"] = (root / manifest).read_text(errors="ignore")[:1000]
                break

    return found, pkg_data


def _determine_roles(
    root: Path,
    stack: list[str],
    pkg_data: dict[str, Any],
) -> list[str]:
    """Return relevant roles for this repo, deduped and ordered."""
    roles: set[str] = set()

    for s in stack:
        for r in _STACK_TO_ROLES.get(s, []):
            roles.add(r)

    # Refine node roles from dep analysis
    if "node" in pkg_data:
        deps = pkg_data["node"].get("all_deps", set())
        if deps & _NODE_FRONTEND_DEPS:
            roles.add("frontend_dev")
        if deps & _NODE_BACKEND_DEPS:
            roles.add("backend_dev")
        # TypeScript → already covered

    # AI/ML libs
    if "python" in pkg_data:
        deps = pkg_data["python"].get("deps", set())
        if deps & _AI_DEPS:
            roles.add("ai_eng")

    # Extra detectors
    for patterns, role in _EXTRA_ROLE_DETECTORS:
        for pattern in patterns:
            if "*" in pattern:
                if list(root.glob(pattern)):
                    roles.add(role)
                    break
            elif (root / pattern).exists():
                roles.add(role)
                break

    # Always include ba if there are markdown docs
    if list(root.glob("docs/**/*.md")) or (root / "README.md").exists():
        roles.add("ba")

    # Canonical order
    order = ["backend_dev", "frontend_dev", "db_eng", "qa_backend", "ai_eng", "sre", "ba"]
    return [r for r in order if r in roles]


def _build_structure_doc(
    root: Path,
    stack: list[str],
    pkg_data: dict[str, Any],
) -> str:
    """Build a concise structural summary document for this repo."""
    lines: list[str] = [
        f"# Project Structure: {root.name}",
        "",
        f"**Stack:** {', '.join(stack) if stack else 'unknown'}",
        f"**Root:** `{root}`",
        "",
        "## Directory Tree (top 2 levels)",
        "```",
    ]

    for item in sorted(root.iterdir()):
        if item.name.startswith(".") and item.name not in (".github", ".env.example"):
            continue
        if item.name in ("node_modules", "__pycache__", ".git", "dist", "build", ".venv", "venv"):
            continue
        prefix = "📁 " if item.is_dir() else "📄 "
        lines.append(f"{prefix}{item.name}")
        if item.is_dir():
            try:
                children = sorted(item.iterdir())[:8]
                for child in children:
                    if child.name.startswith("__"):
                        continue
                    lines.append(f"   {'📁' if child.is_dir() else '📄'} {child.name}")
            except PermissionError:
                pass

    lines.append("```")

    # Package manifest summaries
    if "node" in pkg_data:
        nd = pkg_data["node"]
        lines += ["", "## package.json summary", "```json"]
        lines.append(f"  name:        {nd.get('name', '')} v{nd.get('version', '')}")
        lines.append(f"  description: {nd.get('description', '')}")
        deps = nd.get("all_deps", set())
        if deps:
            lines.append(f"  key deps:    {', '.join(sorted(deps)[:20])}")
        lines.append("```")

    if "python" in pkg_data:
        pd = pkg_data["python"]
        lines += ["", "## pyproject.toml / requirements summary", "```toml"]
        if pd.get("name"):
            lines.append(f"  name: {pd['name']}")
        if pd.get("deps"):
            lines.append(f"  key deps: {', '.join(sorted(pd['deps'])[:20])}")
        lines.append("```")

    if "go" in pkg_data:
        lines += ["", "## go.mod (excerpt)", "```", pkg_data["go"][:500], "```"]

    return "\n".join(lines)


def _parse_package_json(path: Path) -> dict[str, Any]:
    import json
    try:
        data = json.loads(path.read_text(errors="ignore"))
        deps: set[str] = set()
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update(data.get(section, {}).keys())
        return {
            "name":        data.get("name", ""),
            "version":     data.get("version", ""),
            "description": data.get("description", ""),
            "all_deps":    deps,
        }
    except Exception:
        return {}


def _parse_pyproject(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(errors="ignore")
        # Extract deps from [project.dependencies] or [tool.poetry.dependencies]
        deps: set[str] = set()
        for line in text.splitlines():
            m = re.match(r'^\s+"?([a-zA-Z][a-zA-Z0-9_-]+)', line)
            if m:
                pkg = m.group(1).lower().replace("-", "_")
                if len(pkg) > 2:
                    deps.add(pkg)
        name_m = re.search(r'^name\s*=\s*["\']([^"\']+)', text, re.M)
        return {"name": name_m.group(1) if name_m else "", "deps": deps}
    except Exception:
        return {}


def _is_ingestible(path: Path) -> bool:
    """Only ingest text-based, non-giant files."""
    if path.suffix.lower() not in {".md", ".mdx", ".txt", ".rst", ".yml",
                                    ".yaml", ".toml", ".prisma", ".sql", ".json"}:
        return False
    try:
        size = path.stat().st_size
        return size < 500_000   # skip files > 500 KB
    except OSError:
        return False
