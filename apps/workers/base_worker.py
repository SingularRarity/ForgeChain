"""Base worker — tiered LLM routing, DSPy prompt optimisation, token ledger.

Pipeline per job:
  1. Redact PII
  2. Determine base tier for this role (junior/mid/senior)
  3. Run DSPy module at base tier → get patch + confidence
  4. If confidence == "low" → auto-escalate one tier and retry (max once)
  5. If Senior output sets cto_flag == "yes" → run CTO review (Claude)
  6. Store all ledger entries (tokens + cost per call)
  7. Open GitHub PR, transition state → REVIEW
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Any, Optional

import redis as sync_redis
from github import Github, GithubException

for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redaction import Redactor
from policy import PIIVault, PIIPolicy
from orchestrator import StateMachine, TaskState
from providers.tiered_router import (
    get_route, escalate, ROLE_BASE_TIER, TierRoute
)
from providers.token_ledger import TokenLedger
from dspy_prompts import ForgeChainModule

logger = logging.getLogger(__name__)

_CONFIDENCE_LOW     = "low"
_CTO_FLAG_YES       = "yes"


class BaseWorker(ABC):
    role: str = "base"

    def __init__(self) -> None:
        self._redis_url = os.environ["REDIS_URL"]
        self._redis     = sync_redis.from_url(self._redis_url, decode_responses=True)
        self._redactor  = Redactor()
        self._vault     = PIIVault()
        self._sm        = StateMachine(self._redis_url)
        self._ledger    = TokenLedger(self._redis_url)
        self._github_token = os.getenv("GITHUB_TOKEN")
        self._repo_name    = os.getenv("GITHUB_REPO")

    # ------------------------------------------------------------------ #
    # Subclass contract                                                    #
    # ------------------------------------------------------------------ #

    def role_context(self) -> str:
        """Describe this role's stack and responsibilities (used as DSPy input)."""
        return f"Role: {self.role}"

    def build_inputs(self, task: dict[str, Any], tier: str) -> dict[str, Any]:
        """Build DSPy input kwargs for the given tier signature.

        Subclasses should override to add tier-specific fields like
        `existing_context` (required by mid/senior signatures).
        """
        base = {
            "task_description": task.get("description", ""),
            "role_context": self.role_context(),
        }
        if tier in ("mid", "senior"):
            base["existing_context"] = task.get("context", "")
        # BA role
        if task.get("jira_ticket"):
            base["jira_body"]       = task["jira_ticket"]
            base["project_context"] = task.get("context", "")
        # QA role
        if self.role == "qa_backend":
            base["feature_description"] = task.get("description", "")
            base["implementation_hint"] = task.get("context", "")
        return base

    # ------------------------------------------------------------------ #
    # Entry point                                                          #
    # ------------------------------------------------------------------ #

    def process(self, task_id: str) -> None:
        asyncio.run(self._async_process(task_id))

    async def _async_process(self, task_id: str) -> None:
        task = await self._sm.get(task_id)
        if task is None:
            logger.error("Task %s not found", task_id)
            return

        logger.info("[%s] Starting task %s", self.role, task_id)

        try:
            await self._sm.transition(task_id, TaskState.RUNNING)

            # 1. PII redaction
            policy = PIIPolicy.strict()
            raw_desc = task.get("description", "")
            if policy.redact_before_prompt:
                result = self._redactor.redact(raw_desc)
                task = {**task, "description": result.redacted_text}
                for placeholder, original in result.pii_map.items():
                    ref = self._vault.store(original)
                    logger.debug("[%s] PII vaulted: %s → %s", self.role, placeholder, ref)

            # 2. Determine starting tier
            base_tier = task.get("tier") or ROLE_BASE_TIER.get(self.role, "mid")
            route = get_route(self.role, base_tier)  # type: ignore[arg-type]

            # 3. First LLM call at base tier
            prediction, tokens = await self._run_dspy(task, route, stage="draft")

            # 4. Auto-escalate on low confidence
            patch         = getattr(prediction, "patch",   None) or getattr(prediction, "test_file", None) or getattr(prediction, "summary", "")
            confidence    = getattr(prediction, "confidence", "medium").strip().lower()
            self_review   = getattr(prediction, "self_review", "")
            arch_notes    = getattr(prediction, "architecture_notes", "")
            cto_flag      = getattr(prediction, "cto_flag", "no").strip().lower()

            if confidence == _CONFIDENCE_LOW and route.tier != "senior":
                logger.info("[%s] Low confidence at %s — escalating", self.role, route.tier)
                escalated_tier = escalate(route.tier, self.role)
                route = get_route(self.role, escalated_tier)
                prediction, tokens = await self._run_dspy(task, route, stage="escalation")
                patch       = getattr(prediction, "patch",    None) or getattr(prediction, "test_file", None) or getattr(prediction, "summary", "")
                self_review = getattr(prediction, "self_review",        "")
                arch_notes  = getattr(prediction, "architecture_notes", "")
                cto_flag    = getattr(prediction, "cto_flag", "no").strip().lower()

            # 5. CTO review if senior flagged it
            cto_verdict = ""
            cto_guidance = ""
            if cto_flag == _CTO_FLAG_YES:
                logger.info("[%s] Senior flagged CTO review for task %s", self.role, task_id)
                cto_pred, cto_tokens = await self._run_cto_review(
                    task_description=task.get("description", ""),
                    senior_patch=patch or "",
                    senior_notes=arch_notes,
                    task_id=task_id,
                )
                cto_verdict  = getattr(cto_pred, "verdict",              "")
                cto_guidance = getattr(cto_pred, "architecture_guidance", "")
                security_flags = getattr(cto_pred, "security_flags",     "none")
                refactor_plan  = getattr(cto_pred, "refactor_plan",      "")

                # If CTO says revise, apply refactor plan as extra context and re-run senior
                if "revise" in cto_verdict.lower():
                    logger.info("[%s] CTO requested revision — re-running Senior with refactor plan", self.role)
                    revised_task = {
                        **task,
                        "context": f"CTO refactor guidance:\n{refactor_plan}\n\nOriginal context:\n{task.get('context','')}"
                    }
                    route = get_route(self.role, "senior")
                    prediction, _ = await self._run_dspy(revised_task, route, stage="cto_revision")
                    patch      = getattr(prediction, "patch", patch)
                    arch_notes = getattr(prediction, "architecture_notes", arch_notes)

            # 6. Persist to Redis
            self._redis.hset(
                f"forgechain:task:{task_id}",
                mapping={k: str(v) for k, v in {
                    "patch":         patch or "",
                    "self_review":   self_review,
                    "arch_notes":    arch_notes,
                    "cto_verdict":   cto_verdict,
                    "cto_guidance":  cto_guidance,
                    "tier_used":     route.tier,
                    "provider":      route.provider,
                    "model":         route.model,
                }.items()},
            )

            # 7. GitHub PR
            pr_url = ""
            if self._github_token and self._repo_name and patch:
                pr_url = self._open_pr(task_id, task, patch, route)

            await self._sm.transition(task_id, TaskState.REVIEW, extra={"pr_url": pr_url})
            logger.info("[%s] Task %s → REVIEW (tier=%s)", self.role, task_id, route.tier)

        except Exception as exc:
            logger.exception("[%s] Task %s failed", self.role, task_id)
            await self._sm.transition(task_id, TaskState.FAILED, extra={"error": str(exc)})

    # ------------------------------------------------------------------ #
    # DSPy execution helpers                                               #
    # ------------------------------------------------------------------ #

    async def _run_dspy(
        self,
        task: dict[str, Any],
        route: TierRoute,
        stage: str,
    ) -> tuple[Any, int]:
        """Run DSPy module synchronously in executor (DSPy is sync)."""
        module = ForgeChainModule(self.role, route.tier)  # type: ignore[arg-type]
        inputs = self.build_inputs(task, route.tier)

        loop = asyncio.get_event_loop()
        prediction = await loop.run_in_executor(None, lambda: module.run(**inputs))

        # Estimate tokens from prediction text (DSPy doesn't expose raw usage yet)
        output_text = " ".join(str(v) for v in prediction.values() if v)
        prompt_tokens     = len(" ".join(str(v) for v in inputs.values())) // 4
        completion_tokens = len(output_text) // 4

        await self._ledger.record(
            task_id=task["task_id"],
            role=self.role,
            tier=route.tier,
            stage=stage,
            provider=route.provider,
            model=route.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return prediction, prompt_tokens + completion_tokens

    async def _run_cto_review(
        self,
        task_description: str,
        senior_patch: str,
        senior_notes: str,
        task_id: str,
    ) -> tuple[Any, int]:
        cto_module = ForgeChainModule(self.role, "cto")
        loop = asyncio.get_event_loop()
        prediction = await loop.run_in_executor(
            None,
            lambda: cto_module.run_cto_review(
                task_description=task_description,
                senior_patch=senior_patch,
                senior_notes=senior_notes,
            ),
        )
        output_text = " ".join(str(v) for v in prediction.values() if v)
        prompt_tokens     = (len(task_description) + len(senior_patch) + len(senior_notes)) // 4
        completion_tokens = len(output_text) // 4
        cto_route = get_route(self.role, "cto")

        await self._ledger.record(
            task_id=task_id,
            role=self.role,
            tier="cto",
            stage="cto_review",
            provider=cto_route.provider,
            model=cto_route.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return prediction, prompt_tokens + completion_tokens

    # ------------------------------------------------------------------ #
    # GitHub PR                                                            #
    # ------------------------------------------------------------------ #

    def _open_pr(
        self,
        task_id: str,
        task: dict[str, Any],
        patch: str,
        route: TierRoute,
    ) -> str:
        try:
            gh   = Github(self._github_token)
            repo = gh.get_repo(self._repo_name)
            branch = f"forgechain/{self.role}/{task_id[:8]}"
            base_sha = repo.get_branch(repo.default_branch).commit.sha
            repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)
            repo.create_file(
                path=f"patches/{task_id}.patch",
                message=f"chore(forgechain): {self.role} patch {task_id[:8]}",
                content=patch,
                branch=branch,
            )
            pr = repo.create_pull(
                title=f"[{self.role.upper()}/{route.tier.upper()}] {task.get('description','')[:72]}",
                body=(
                    f"**ForgeChain** — role `{self.role}` | tier `{route.tier}` | "
                    f"provider `{route.provider}/{route.model}`\n\n"
                    f"Task: `{task_id}`\n\n"
                    f"```diff\n{patch[:3000]}\n```\n\n"
                    f"> Requires human approval — agents cannot merge."
                ),
                head=branch,
                base=repo.default_branch,
                draft=True,
            )
            return pr.html_url
        except GithubException as e:
            logger.error("[%s] GitHub PR failed: %s", self.role, e)
            return ""
