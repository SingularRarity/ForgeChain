"""SandboxReport — structured result of validating a patch against a target project.

Attached to the ForgeChain job as `sandbox_report` metadata before REVIEW.
Human reviewers see a pass/fail summary + details without having to read logs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TestSuiteResult:
    framework: str          # pytest | jest | go test | cargo test | etc.
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_s: float
    output_tail: str        # last 2000 chars of test output for review
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.failed == 0 and self.errors == 0

    def summary(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        return (
            f"[{status}] {self.framework}: "
            f"{self.passed} passed, {self.failed} failed, "
            f"{self.errors} errors, {self.skipped} skipped "
            f"({self.duration_s:.1f}s)"
        )


@dataclass
class ABEndpointResult:
    method: str
    path: str
    status_before: int
    status_after: int
    schema_match: bool
    latency_before_ms: float
    latency_after_ms: float
    diffs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status_before == self.status_after and self.schema_match

    def summary(self) -> str:
        status = "✓" if self.ok else "✗"
        delta = self.latency_after_ms - self.latency_before_ms
        delta_str = f"+{delta:.0f}ms" if delta > 0 else f"{delta:.0f}ms"
        s = f"{status} {self.method} {self.path}  ({delta_str})"
        if self.diffs:
            s += "\n    " + "\n    ".join(self.diffs)
        return s


@dataclass
class SmokeTestResult:
    url: str
    passed: int
    failed: int
    screenshots: list[str] = field(default_factory=list)   # paths inside sandbox volume
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        return f"[{status}] Browser smoke: {self.passed} passed, {self.failed} failed"


@dataclass
class SandboxReport:
    """Full validation report for one ForgeChain patch against a target project."""

    task_id: str
    project_id: Optional[str]
    repo_path: str
    patch_preview: str          # first 500 chars of the patch
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    # Sub-results (None = step was skipped)
    test_suite: Optional[TestSuiteResult] = None
    ab_results: list[ABEndpointResult] = field(default_factory=list)
    smoke: Optional[SmokeTestResult] = None

    error: Optional[str] = None  # set if sandbox itself crashed

    @property
    def overall_passed(self) -> bool:
        if self.error:
            return False
        suite_ok  = self.test_suite is None or self.test_suite.ok
        ab_ok     = all(r.ok for r in self.ab_results)
        smoke_ok  = self.smoke is None or self.smoke.ok
        return suite_ok and ab_ok and smoke_ok

    @property
    def duration_s(self) -> float:
        return self.finished_at - self.started_at

    def summary(self) -> str:
        status = "✅ SANDBOX PASSED" if self.overall_passed else "❌ SANDBOX FAILED"
        lines = [status, f"Duration: {self.duration_s:.1f}s"]

        if self.error:
            lines.append(f"Error: {self.error}")
            return "\n".join(lines)

        if self.test_suite:
            lines.append(self.test_suite.summary())

        if self.ab_results:
            lines.append(f"A/B ({len(self.ab_results)} endpoints):")
            for r in self.ab_results:
                lines.append(f"  {r.summary()}")

        if self.smoke:
            lines.append(self.smoke.summary())

        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def skipped(cls, task_id: str, reason: str) -> "SandboxReport":
        r = cls(task_id=task_id, project_id=None, repo_path="", patch_preview="")
        r.error = f"SKIPPED: {reason}"
        r.finished_at = time.time()
        return r
