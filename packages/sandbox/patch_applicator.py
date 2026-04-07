"""PatchApplicator — copy the target project to a temp dir and apply the patch.

Creates two copies:
  baseline/  — clean copy (no patch) → "before"
  patched/   — copy with patch applied → "after"

Both copies are available for docker-compose or direct test execution.
The temp dir is cleaned up when the context manager exits.

Supports unified diff format (what ForgeChain workers produce).
Falls back to writing the patch as a .patch file and using `patch -p1`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)


class PatchApplicator:
    def __init__(self, repo_path: str, patch: str) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.patch = patch

    @contextmanager
    def apply(self) -> Generator[tuple[Path, Path], None, None]:
        """Context manager: yields (baseline_dir, patched_dir) as temp Paths.

        Both are complete copies of the repo. patched_dir has the patch applied.
        Cleans up on exit.
        """
        tmp = Path(tempfile.mkdtemp(prefix="forgechain_sandbox_"))
        baseline = tmp / "baseline"
        patched  = tmp / "patched"

        try:
            logger.info("[sandbox] Copying %s → %s / %s", self.repo_path, baseline.name, patched.name)
            shutil.copytree(self.repo_path, baseline, symlinks=True)
            shutil.copytree(self.repo_path, patched,  symlinks=True)

            # Apply patch to the patched copy
            applied = self._apply_patch(patched)
            if not applied:
                logger.warning("[sandbox] Patch application failed — patched dir is identical to baseline")

            yield baseline, patched
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            logger.debug("[sandbox] Cleaned up temp dir %s", tmp)

    def _apply_patch(self, target: Path) -> bool:
        """Write patch to a temp file and apply via `patch -p1`. Returns True on success."""
        if not self.patch or not self.patch.strip():
            logger.warning("[sandbox] Empty patch — nothing to apply")
            return False

        # Write patch file
        patch_file = target / "_forgechain.patch"
        patch_file.write_text(self.patch, encoding="utf-8")

        try:
            result = subprocess.run(
                ["patch", "-p1", "--batch", "--forward", f"--input={patch_file}"],
                cwd=target,
                capture_output=True,
                text=True,
                timeout=30,
            )
            patch_file.unlink(missing_ok=True)

            if result.returncode == 0:
                logger.info("[sandbox] Patch applied successfully")
                return True
            else:
                logger.warning(
                    "[sandbox] patch exited %d:\n%s\n%s",
                    result.returncode, result.stdout[:500], result.stderr[:500],
                )
                # Try git apply as fallback
                return self._apply_via_git(target)

        except FileNotFoundError:
            # `patch` command not available — try git apply
            patch_file.unlink(missing_ok=True)
            return self._apply_via_git(target)
        except subprocess.TimeoutExpired:
            logger.error("[sandbox] patch command timed out")
            patch_file.unlink(missing_ok=True)
            return False

    def _apply_via_git(self, target: Path) -> bool:
        """Fallback: use `git apply` if the target is a git repo."""
        patch_file = target / "_forgechain.patch"
        patch_file.write_text(self.patch, encoding="utf-8")
        try:
            result = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(patch_file)],
                cwd=target,
                capture_output=True,
                text=True,
                timeout=30,
            )
            patch_file.unlink(missing_ok=True)
            if result.returncode == 0:
                logger.info("[sandbox] Patch applied via git apply")
                return True
            logger.warning("[sandbox] git apply also failed: %s", result.stderr[:300])
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("[sandbox] git apply unavailable: %s", exc)
            patch_file.unlink(missing_ok=True)
            return False
