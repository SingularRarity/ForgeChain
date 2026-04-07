"""ABComparator — compare API responses before and after a patch.

Sends the same HTTP requests to the baseline environment (before patch)
and the patched environment (after patch) in parallel, then compares:
  - Status codes must match
  - Response JSON schema (key presence) must match
  - Values are allowed to differ (timestamps, IDs, etc.)

The endpoint list is auto-discovered from the project's OpenAPI spec
(/docs, /openapi.json, /swagger.json) or falls back to a minimal probe set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .report import ABEndpointResult

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_ENDPOINTS = 20   # cap auto-discovery to keep sandbox runs fast

# Probe set used when OpenAPI discovery fails
_FALLBACK_PROBES: list[dict] = [
    {"method": "GET",  "path": "/"},
    {"method": "GET",  "path": "/health"},
    {"method": "GET",  "path": "/healthz"},
    {"method": "GET",  "path": "/api/health"},
    {"method": "GET",  "path": "/api/v1/health"},
    {"method": "GET",  "path": "/status"},
]


class ABComparator:
    def __init__(self, baseline_url: str, patched_url: str) -> None:
        self.baseline_url = baseline_url.rstrip("/")
        self.patched_url  = patched_url.rstrip("/")

    async def compare_all(self) -> list[ABEndpointResult]:
        """Discover endpoints from OpenAPI, then compare each one."""
        probes = await self._discover_probes()
        if not probes:
            probes = _FALLBACK_PROBES

        tasks = [self._compare_one(p["method"], p["path"], p.get("body")) for p in probes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("[sandbox:ab] Probe error: %s", r)
            else:
                out.append(r)
        return out

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    async def _discover_probes(self) -> list[dict]:
        """Try to read /openapi.json from the baseline and extract GET endpoints."""
        for spec_path in ("/openapi.json", "/docs/openapi.json", "/swagger.json", "/api-docs"):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.baseline_url}{spec_path}")
                    if resp.is_success:
                        spec = resp.json()
                        return self._probes_from_spec(spec)
            except Exception:
                continue
        logger.debug("[sandbox:ab] OpenAPI not found — using fallback probes")
        return []

    @staticmethod
    def _probes_from_spec(spec: dict) -> list[dict]:
        """Extract safe GET probes from an OpenAPI spec (no auth-required paths)."""
        probes = []
        paths = spec.get("paths", {})
        for path, methods in paths.items():
            if "{" in path:
                continue   # skip parameterised paths — no test IDs available
            if "get" in methods:
                op = methods["get"]
                # Skip endpoints that clearly need auth
                security = op.get("security", spec.get("security", []))
                if security and security != [{}]:
                    continue
                probes.append({"method": "GET", "path": path})
            if len(probes) >= _MAX_ENDPOINTS:
                break
        return probes

    async def _compare_one(
        self,
        method: str,
        path: str,
        body: Any = None,
    ) -> ABEndpointResult:
        baseline_task = asyncio.create_task(self._request(self.baseline_url, method, path, body))
        patched_task  = asyncio.create_task(self._request(self.patched_url,  method, path, body))
        (b_status, b_body, b_ms), (p_status, p_body, p_ms) = await asyncio.gather(
            baseline_task, patched_task
        )

        schema_match, diffs = _schema_compare(b_body, p_body)

        return ABEndpointResult(
            method=method,
            path=path,
            status_before=b_status,
            status_after=p_status,
            schema_match=schema_match,
            latency_before_ms=b_ms,
            latency_after_ms=p_ms,
            diffs=diffs,
        )

    async def _request(
        self,
        base: str,
        method: str,
        path: str,
        body: Any = None,
    ) -> tuple[int, Any, float]:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(method, f"{base}{path}", json=body)
            ms = (time.monotonic() - start) * 1000
            try:
                parsed = resp.json()
            except Exception:
                parsed = resp.text
            return resp.status_code, parsed, ms
        except Exception as exc:
            ms = (time.monotonic() - start) * 1000
            logger.debug("[sandbox:ab] Request error %s%s: %s", base, path, exc)
            return 0, None, ms


# ---------------------------------------------------------------------------
# Schema comparison (structure only — values can differ)
# ---------------------------------------------------------------------------

def _schema_compare(a: Any, b: Any, path: str = "root") -> tuple[bool, list[str]]:
    diffs: list[str] = []

    if a is None and b is None:
        return True, []

    if type(a) is not type(b):
        # Allow int/float interchangeability
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return True, []
        diffs.append(f"{path}: type mismatch ({type(a).__name__} vs {type(b).__name__})")
        return False, diffs

    if isinstance(a, dict):
        keys_a, keys_b = set(a), set(b)
        for k in keys_a - keys_b:
            diffs.append(f"{path}.{k}: key in baseline but missing after patch")
        for k in keys_b - keys_a:
            diffs.append(f"{path}.{k}: new key introduced by patch (may be intentional)")
        for k in keys_a & keys_b:
            _, sub = _schema_compare(a[k], b[k], f"{path}.{k}")
            diffs.extend(sub)

    elif isinstance(a, list) and a and b:
        _, sub = _schema_compare(a[0], b[0], f"{path}[0]")
        diffs.extend(sub)

    return len(diffs) == 0, diffs
