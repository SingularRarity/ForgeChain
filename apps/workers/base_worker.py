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
from knowledge import Retriever
from notify import dispatcher as _notifier
from sandbox import SandboxRunner

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
        self._retriever = Retriever(self.role)
        # Global fallbacks — overridden per-project at task run time via registry
        self._github_token   = os.getenv("GITHUB_TOKEN", "")
        self._repo_name      = os.getenv("GITHUB_REPO", "")
        # Branch workers open PRs against. Defaults to "dev" so main is always human-gated.
        self._target_branch  = os.getenv("FORGECHAIN_TARGET_BRANCH", "dev")

    # ------------------------------------------------------------------ #
    # Subclass contract                                                    #
    # ------------------------------------------------------------------ #

    def role_context(self) -> str:
        """Describe this role's stack and responsibilities (used as DSPy input)."""
        return f"Role: {self.role}"

    def build_inputs(self, task: dict[str, Any], tier: str) -> dict[str, Any]:
        """Build DSPy input kwargs for the given tier signature.

        Retrieves relevant knowledge from the local ChromaDB knowledge base
        and injects it as `retrieved_knowledge` into every signature tier.
        The retrieval query is the task description — same text the model
        will work on, so cosine similarity finds the most applicable docs.
        """
        description = task.get("description", "")

        # RAG: use project-scoped retriever if a project is attached to this task
        project_id = task.get("project") or None
        retriever = (
            Retriever(self.role, project_id=project_id)
            if project_id
            else self._retriever
        )
        retrieved_knowledge = retriever.retrieve_as_context(description)
        if retrieved_knowledge:
            logger.info(
                "[%s] RAG: injected %d chars of knowledge into prompt",
                self.role, len(retrieved_knowledge),
            )
        else:
            logger.debug("[%s] RAG: no knowledge base hits for this task", self.role)

        base: dict[str, Any] = {
            "task_description":    description,
            "role_context":        self.role_context(),
            "retrieved_knowledge": retrieved_knowledge,
        }
        if tier in ("mid", "senior"):
            base["existing_context"] = task.get("context", "")
        # BA role
        if task.get("jira_ticket"):
            base["jira_body"]       = task["jira_ticket"]
            base["project_context"] = task.get("context", "")
        # QA role
        if self.role == "qa_backend":
            base["feature_description"] = description
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

            # 7. Sandbox validation — run target project's tests + A/B + smoke
            #    Runs only when FORGECHAIN_SANDBOX_ENABLED=1 and task has a project
            #    with a local repo_path. Always non-blocking: failures attach a report
            #    but do NOT prevent the job from reaching REVIEW.
            sandbox_report_json = ""
            if patch:
                try:
                    repo_path = task.get("repo_path") or await self._resolve_repo_path(
                        task.get("project")
                    )
                    sb_runner = SandboxRunner(self._redis_url)
                    sb_report = await sb_runner.validate(
                        task_id=task_id,
                        patch=patch,
                        repo_path=repo_path or "",
                        project_id=task.get("project"),
                    )
                    sandbox_report_json = sb_report.to_json()
                    logger.info(
                        "[%s] Sandbox: %s",
                        self.role,
                        "PASSED" if sb_report.overall_passed else "FAILED",
                    )
                    self._redis.hset(
                        f"forgechain:task:{task_id}",
                        "sandbox_report", sandbox_report_json,
                    )
                except Exception as exc:
                    logger.warning("[%s] Sandbox runner error (non-fatal): %s", self.role, exc)

            # 8. GitHub PR — resolve per-project credentials, fall back to global env
            pr_url = ""
            if patch:
                gh_token, gh_repo = await self._resolve_github_creds(task.get("project"))
                if gh_token and gh_repo:
                    pr_url = self._open_pr(task_id, task, patch, route, gh_token, gh_repo)

            await self._sm.transition(task_id, TaskState.REVIEW, extra={"pr_url": pr_url})
            logger.info("[%s] Task %s → REVIEW (tier=%s)", self.role, task_id, route.tier)
            asyncio.create_task(_notifier.notify(
                "job_review",
                task_id=task_id,
                role=self.role,
                tier=route.tier,
                pr_url=pr_url,
                description=task.get("description", ""),
            ))

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

    async def _resolve_repo_path(self, project_id: Optional[str]) -> Optional[str]:
        """Look up repo_path from the project registry for sandbox validation."""
        if not project_id:
            return None
        try:
            import redis.asyncio as _aioredis
            r = _aioredis.from_url(self._redis_url, decode_responses=True)
            try:
                path = await r.hget(f"forgechain:registry:project:{project_id}", "repo_path")
                return path or None
            finally:
                await r.aclose()
        except Exception:
            return None

    async def _resolve_github_creds(
        self, project_id: Optional[str]
    ) -> tuple[str, str]:
        """Return (github_token, github_repo) for this task.

        Priority:
          1. Per-project credentials stored in the registry (best: scoped PAT)
          2. Global GITHUB_TOKEN / GITHUB_REPO env vars (fallback)
        """
        if project_id:
            try:
                import redis.asyncio as _aioredis
                r = _aioredis.from_url(self._redis_url, decode_responses=True)
                try:
                    data = await r.hmget(
                        f"forgechain:registry:project:{project_id}",
                        "github_token", "github_repo",
                    )
                    token = data[0] or ""
                    repo  = data[1] or ""
                    if token and repo:
                        return token, repo
                    # Partial: one field set, use the other from global env
                    return token or self._github_token, repo or self._repo_name
                finally:
                    await r.aclose()
            except Exception:
                pass
        return self._github_token, self._repo_name

    def _open_pr(
        self,
        task_id: str,
        task: dict[str, Any],
        patch: str,
        route: TierRoute,
        github_token: str = "",
        github_repo: str = "",
    ) -> str:
        token = github_token or self._github_token
        repo_name = github_repo or self._repo_name
        try:
            gh   = Github(token)
            repo = gh.get_repo(repo_name)
            branch = f"forgechain/{self.role}/{task_id[:8]}"
            # Target the configured base branch; fall back to repo default if it doesn't exist
            try:
                base_ref = repo.get_branch(self._target_branch)
                base_branch_name = self._target_branch
            except GithubException:
                base_ref = repo.get_branch(repo.default_branch)
                base_branch_name = repo.default_branch
                logger.warning(
                    "[%s] Branch %r not found — falling back to %r",
                    self.role, self._target_branch, base_branch_name,
                )
            repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_ref.commit.sha)
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
                    f"> Auto-generated. Merge into `{base_branch_name}` — "
                    f"then open a PR from `{base_branch_name}` → `main` for human review."
                ),
                head=branch,
                base=base_branch_name,
                draft=True,
            )
            return pr.html_url
        except GithubException as e:
            logger.error("[%s] GitHub PR failed: %s", self.role, e)
            return ""
