"""SmokeTester — Playwright frontend smoke test against the patched environment.

Runs a lightweight browser check to verify the frontend still loads and
renders basic content after the patch. Not a full E2E suite — just enough
to catch a broken import, a missing build, or a crashed API connection.

Requires playwright to be installed in the worker environment:
    pip install playwright && playwright install chromium --with-deps

If playwright is not installed, the test is skipped gracefully.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .report import SmokeTestResult

logger = logging.getLogger(__name__)

_SMOKE_TIMEOUT = 30_000   # ms


class SmokeTester:
    def __init__(self, frontend_url: str, screenshot_dir: Path | None = None) -> None:
        self.frontend_url   = frontend_url
        self.screenshot_dir = screenshot_dir

    async def run(self) -> SmokeTestResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.info("[sandbox:smoke] playwright not installed — skipping browser smoke")
            return SmokeTestResult(url=self.frontend_url, passed=0, failed=0,
                                   errors=["playwright not installed"])

        passed = 0
        failed = 0
        errors: list[str] = []
        screenshots: list[str] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 720})
                page.set_default_timeout(_SMOKE_TIMEOUT)

                # ── Check 1: Page loads without 500 error ────────────── #
                try:
                    resp = await page.goto(self.frontend_url, wait_until="networkidle")
                    if resp and resp.status < 500:
                        passed += 1
                        logger.debug("[sandbox:smoke] Page loaded: %s", resp.status)
                    else:
                        failed += 1
                        errors.append(f"Page returned status {resp.status if resp else 'no response'}")
                    shot = await self._screenshot(page, "01_homepage.png")
                    if shot: screenshots.append(shot)
                except Exception as exc:
                    failed += 1
                    errors.append(f"Page load failed: {exc}")

                # ── Check 2: No uncaught JS errors ───────────────────── #
                js_errors: list[str] = []
                page.on("pageerror", lambda e: js_errors.append(str(e)))

                await page.wait_for_timeout(2000)   # let JS settle

                if js_errors:
                    failed += 1
                    errors.append(f"JS errors: {'; '.join(js_errors[:3])}")
                else:
                    passed += 1

                # ── Check 3: Body has visible content ────────────────── #
                try:
                    body_text = await page.inner_text("body")
                    if len(body_text.strip()) > 20:
                        passed += 1
                    else:
                        failed += 1
                        errors.append("Body appears empty after render")
                except Exception as exc:
                    failed += 1
                    errors.append(f"Body check failed: {exc}")

                # ── Check 4: API connectivity (frontend → backend) ────── #
                # Most frontends show an error banner if the API is down.
                # Look for common error indicators.
                try:
                    content = await page.content()
                    bad_signs = [
                        "network error", "failed to fetch", "connection refused",
                        "502 bad gateway", "503 service unavailable",
                    ]
                    found = [s for s in bad_signs if s in content.lower()]
                    if found:
                        failed += 1
                        errors.append(f"API connectivity errors in page: {found}")
                        shot = await self._screenshot(page, "02_api_error.png")
                        if shot: screenshots.append(shot)
                    else:
                        passed += 1
                except Exception as exc:
                    errors.append(f"API connectivity check failed: {exc}")

            finally:
                await browser.close()

        return SmokeTestResult(
            url=self.frontend_url,
            passed=passed,
            failed=failed,
            screenshots=screenshots,
            errors=errors,
        )

    async def _screenshot(self, page, name: str) -> str | None:
        if self.screenshot_dir is None:
            return None
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = self.screenshot_dir / name
            await page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as exc:
            logger.debug("[sandbox:smoke] Screenshot failed: %s", exc)
            return None
