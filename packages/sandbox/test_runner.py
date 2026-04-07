"""TestRunner — detect and run the target project's own test suite.

Detects test framework from project manifests, runs the appropriate command,
and parses the output into a TestSuiteResult.

Supported frameworks (auto-detected):
  pytest     — pyproject.toml / setup.py / pytest.ini / tests/ dir
  jest       — package.json with jest or @jest/core dependency
  vitest     — package.json with vitest dependency
  go test    — go.mod present
  cargo test — Cargo.toml present
  rspec      — Gemfile with rspec
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from .report import TestSuiteResult

logger = logging.getLogger(__name__)

_TIMEOUT = 300   # 5 minutes max for test suite
_TAIL_CHARS = 3000


class TestRunner:
    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = Path(repo_dir)

    def run(self) -> TestSuiteResult:
        """Detect framework and run tests. Returns result even on failure."""
        framework, cmd = self._detect()
        if cmd is None:
            return TestSuiteResult(
                framework="none",
                passed=0, failed=0, errors=0, skipped=0,
                duration_s=0.0,
                output_tail="No test suite detected in this project.",
                exit_code=0,
            )

        logger.info("[sandbox:test] Running %s: %s", framework, " ".join(cmd))
        import time
        start = time.monotonic()

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                env={**os.environ, "CI": "true", "FORCE_COLOR": "0"},
            )
        except subprocess.TimeoutExpired:
            return TestSuiteResult(
                framework=framework,
                passed=0, failed=0, errors=1, skipped=0,
                duration_s=_TIMEOUT,
                output_tail=f"Test suite timed out after {_TIMEOUT}s.",
                exit_code=124,
            )

        duration = time.monotonic() - start
        output = (result.stdout + result.stderr)[-_TAIL_CHARS:]
        passed, failed, errors, skipped = self._parse_counts(framework, result.stdout + result.stderr)

        return TestSuiteResult(
            framework=framework,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            duration_s=round(duration, 2),
            output_tail=output,
            exit_code=result.returncode,
        )

    # ------------------------------------------------------------------ #
    # Framework detection                                                  #
    # ------------------------------------------------------------------ #

    def _detect(self) -> tuple[str, list[str] | None]:
        root = self.repo_dir

        # Python / pytest
        if any([
            (root / "pyproject.toml").exists(),
            (root / "pytest.ini").exists(),
            (root / "setup.cfg").exists(),
            (root / "tests").is_dir(),
            (root / "test").is_dir(),
        ]):
            # Use uv if available (faster), else fall back to python -m pytest
            pytest_cmd = ["uv", "run", "pytest", "--tb=short", "-q", "--no-header"]
            if not _cmd_exists("uv"):
                pytest_cmd = ["python", "-m", "pytest", "--tb=short", "-q", "--no-header"]
            return "pytest", pytest_cmd

        # Node — jest / vitest
        pkg = root / "package.json"
        if pkg.exists():
            text = pkg.read_text(encoding="utf-8", errors="ignore")
            if "vitest" in text:
                return "vitest", ["npx", "vitest", "run", "--reporter=verbose"]
            if "jest" in text or "@jest" in text:
                return "jest", ["npx", "jest", "--ci", "--forceExit"]
            # Generic npm test
            return "npm-test", ["npm", "test", "--", "--watchAll=false"]

        # Go
        if (root / "go.mod").exists():
            return "go-test", ["go", "test", "./...", "-v", "-count=1"]

        # Rust
        if (root / "Cargo.toml").exists():
            return "cargo-test", ["cargo", "test", "--", "--nocapture"]

        # Ruby
        if (root / "Gemfile").exists():
            text = (root / "Gemfile").read_text(encoding="utf-8", errors="ignore")
            if "rspec" in text:
                return "rspec", ["bundle", "exec", "rspec", "--format", "progress"]

        return "none", None

    # ------------------------------------------------------------------ #
    # Output parsing                                                       #
    # ------------------------------------------------------------------ #

    def _parse_counts(self, framework: str, output: str) -> tuple[int, int, int, int]:
        """Extract passed/failed/error/skipped counts from test runner output."""
        p = f = e = s = 0

        if framework == "pytest":
            # "5 passed, 1 failed, 2 warnings"
            m = re.search(r"(\d+) passed", output)
            if m: p = int(m.group(1))
            m = re.search(r"(\d+) failed", output)
            if m: f = int(m.group(1))
            m = re.search(r"(\d+) error", output)
            if m: e = int(m.group(1))
            m = re.search(r"(\d+) skipped", output)
            if m: s = int(m.group(1))

        elif framework in ("jest", "vitest"):
            # "Tests: 3 passed, 1 failed, 4 total"
            m = re.search(r"(\d+) passed", output)
            if m: p = int(m.group(1))
            m = re.search(r"(\d+) failed", output)
            if m: f = int(m.group(1))
            m = re.search(r"(\d+) skipped", output)
            if m: s = int(m.group(1))

        elif framework == "go-test":
            # "ok  pkg  0.123s" or "FAIL pkg"
            p = output.count("\n--- PASS:")
            f = output.count("\n--- FAIL:")

        elif framework == "cargo-test":
            # "test result: ok. 5 passed; 1 failed"
            m = re.search(r"(\d+) passed", output)
            if m: p = int(m.group(1))
            m = re.search(r"(\d+) failed", output)
            if m: f = int(m.group(1))

        elif framework == "rspec":
            # "5 examples, 1 failure"
            m = re.search(r"(\d+) examples?", output)
            total = int(m.group(1)) if m else 0
            m = re.search(r"(\d+) failures?", output)
            if m: f = int(m.group(1))
            p = total - f

        return p, f, e, s


def _cmd_exists(cmd: str) -> bool:
    import shutil as sh
    return sh.which(cmd) is not None
