"""ProjectEnvironment — spin up a target project's own docker-compose in isolation.

Reads the project's docker-compose.yml, rewrites ports to an isolated range,
and starts containers under a unique project name so they don't clash with
the real running instance.

Requires the worker container to have access to the Docker socket:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

Lifecycle:
    async with ProjectEnvironment(repo_dir, project_name="boli_sandbox_abc123") as env:
        api_url = env.api_url          # e.g. http://localhost:18001
        frontend_url = env.frontend_url  # e.g. http://localhost:13001
        # run tests ...
    # containers stopped and removed on exit
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Port range for sandbox environments (shifted by 10000 from typical defaults)
_PORT_OFFSET = 10000
_STARTUP_TIMEOUT = 120   # seconds to wait for services to become healthy
_HEALTH_POLL_INTERVAL = 3


class ProjectEnvironment:
    """Context manager that spins up an isolated copy of a project's Docker stack."""

    def __init__(
        self,
        repo_dir: Path,
        project_name: str,
        env_overrides: Optional[dict[str, str]] = None,
    ) -> None:
        self.repo_dir = Path(repo_dir)
        self.project_name = project_name
        self._env_overrides = env_overrides or {}
        self.api_url: str = ""
        self.frontend_url: str = ""
        self._compose_file: Optional[Path] = None
        self._detected_ports: dict[str, int] = {}

    async def __aenter__(self) -> "ProjectEnvironment":
        self._compose_file = self._find_compose_file()
        if self._compose_file is None:
            raise FileNotFoundError(
                f"No docker-compose.yml found in {self.repo_dir}. "
                "Cannot spin up sandbox environment."
            )

        self._detected_ports = self._detect_ports(self._compose_file)
        await self._start()
        await self._wait_healthy()
        return self

    async def __aexit__(self, *_) -> None:
        await self._stop()

    # ------------------------------------------------------------------ #
    # Port discovery                                                       #
    # ------------------------------------------------------------------ #

    @property
    def api_port(self) -> Optional[int]:
        return self._detected_ports.get("api")

    @property
    def frontend_port(self) -> Optional[int]:
        return self._detected_ports.get("frontend")

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _find_compose_file(self) -> Optional[Path]:
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            candidate = self.repo_dir / name
            if candidate.exists():
                return candidate
        return None

    def _detect_ports(self, compose_file: Path) -> dict[str, int]:
        """Extract host-side port mappings from docker-compose to know where to probe."""
        ports: dict[str, int] = {}
        text = compose_file.read_text(encoding="utf-8")

        # Pattern: "HOST_PORT:CONTAINER_PORT" or "- HOST_PORT:CONTAINER_PORT"
        # Common patterns: 8000:8000, 3000:80, 8080:8080
        for m in re.finditer(r'["\'"]?(\d{4,5}):(?:8000|8080|5000|3000|80)\b', text):
            host_port = int(m.group(1))
            container_port_str = m.group(0).split(":")[1].strip("\"'")
            container_port = int(container_port_str)
            if container_port in (8000, 8080, 5000):
                ports.setdefault("api", host_port)
            elif container_port in (3000, 80):
                ports.setdefault("frontend", host_port)

        # Shift to sandbox range
        sandbox_ports = {}
        for svc, port in ports.items():
            sandbox_ports[svc] = port + _PORT_OFFSET

        if sandbox_ports.get("api"):
            self.api_url = f"http://localhost:{sandbox_ports['api']}"
        if sandbox_ports.get("frontend"):
            self.frontend_url = f"http://localhost:{sandbox_ports['frontend']}"

        return sandbox_ports

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Shift ports in the compose via env vars (projects typically use ${PORT:-default})
        for svc, port in self._detected_ports.items():
            env[f"{svc.upper()}_PORT"] = str(port)
        env.update(self._env_overrides)
        return env

    async def _run_compose(self, *args: str) -> subprocess.CompletedProcess:
        """Run docker compose command in the repo dir under the sandbox project name."""
        cmd = [
            "docker", "compose",
            "-f", str(self._compose_file),
            "-p", self.project_name,
            *args,
        ]
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                cwd=self.repo_dir,
                env=self._build_env(),
                capture_output=True,
                text=True,
                timeout=300,
            ),
        )

    async def _start(self) -> None:
        logger.info("[sandbox] Starting project %r from %s", self.project_name, self.repo_dir)
        result = await self._run_compose("up", "-d", "--build", "--remove-orphans")
        if result.returncode != 0:
            raise RuntimeError(
                f"docker compose up failed for {self.project_name}:\n"
                f"{result.stderr[:1000]}"
            )
        logger.info("[sandbox] Containers started for %r", self.project_name)

    async def _stop(self) -> None:
        logger.info("[sandbox] Stopping project %r", self.project_name)
        result = await self._run_compose("down", "-v", "--remove-orphans")
        if result.returncode != 0:
            logger.warning("[sandbox] docker compose down warnings: %s", result.stderr[:300])

    async def _wait_healthy(self) -> None:
        """Poll the API URL until it responds or timeout."""
        if not self.api_url:
            logger.warning("[sandbox] No API URL detected — skipping health wait")
            return

        deadline = time.monotonic() + _STARTUP_TIMEOUT
        logger.info("[sandbox] Waiting for %s to become healthy ...", self.api_url)

        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(f"{self.api_url}/health")
                    if resp.status_code < 500:
                        logger.info("[sandbox] %s is healthy", self.api_url)
                        return
            except Exception:
                pass
            await asyncio.sleep(_HEALTH_POLL_INTERVAL)

        raise TimeoutError(
            f"Sandbox environment did not become healthy within {_STARTUP_TIMEOUT}s. "
            f"URL: {self.api_url}"
        )
