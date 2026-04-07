"""ForgeChain API routes.

Extends the existing FastAPI app with:
  POST /forgechain/jobs          — enqueue a new coding job
  GET  /forgechain/jobs/{id}     — fetch job state + patch
  POST /forgechain/jobs/{id}/approve  — approve PR (state → APPROVED → DONE)
  POST /forgechain/jobs/{id}/reject   — reject PR  (state → REJECTED → PENDING)
  GET  /forgechain/jobs          — list recent jobs
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

# Orchestrator lives in /packages at runtime
import sys
sys.path.insert(0, "/packages")

from orchestrator import TaskRouter, StateMachine, TaskState
from providers.token_ledger import TokenLedger
from providers.pricing import TIER_DEFAULTS, calculate_cost
from learning.collector import Collector
from quant.coverage import CoverageTracker
from quant.ema import EMATracker
from quant.bandit import BanditRouter

router = APIRouter(prefix="/forgechain", tags=["forgechain"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class JobCreateRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=4000)
    role: Optional[str] = Field(None, description="Explicit agent role; auto-detected if omitted")
    tier: Optional[str] = Field(None, description="Override starting tier: junior|mid|senior (cto requires explicit stage)")
    jira_ticket: Optional[str] = Field(None, description="JIRA ticket body for BA role")
    context: Optional[str] = Field(None, max_length=8000)
    pii_policy: Literal["strict", "permissive"] = "strict"


class JobResponse(BaseModel):
    task_id: str
    state: str
    role: str
    queue: str
    created_at: float
    updated_at: float
    tier_used: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    pr_url: Optional[str] = None
    patch: Optional[str] = None
    cto_verdict: Optional[str] = None
    cto_guidance: Optional[str] = None
    error: Optional[str] = None


class TaskCostResponse(BaseModel):
    task_id: str
    total_tokens: int
    total_cost_usd: float
    calls: list[dict]


class GlobalCostResponse(BaseModel):
    totals: dict[str, float]
    tier_pricing: dict[str, dict]


class ApprovalRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    comment: Optional[str] = None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

import os

def _redis_url() -> str:
    return os.environ["REDIS_URL"]


async def get_state_machine() -> StateMachine:
    return StateMachine(_redis_url())


async def get_redis() -> aioredis.Redis:
    return aioredis.from_url(_redis_url(), decode_responses=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_task_router = TaskRouter()


async def get_ledger() -> TokenLedger:
    return TokenLedger(_redis_url())


def _collector() -> Collector:
    return Collector(_redis_url())


def _serialize_job(data: dict[str, Any]) -> JobResponse:
    return JobResponse(
        task_id=data.get("task_id", ""),
        state=data.get("state", "unknown"),
        role=data.get("role", ""),
        queue=data.get("queue", ""),
        created_at=float(data.get("created_at", 0)),
        updated_at=float(data.get("updated_at", 0)),
        tier_used=data.get("tier_used"),
        provider=data.get("provider"),
        model=data.get("model"),
        pr_url=data.get("pr_url"),
        patch=data.get("patch"),
        cto_verdict=data.get("cto_verdict"),
        cto_guidance=data.get("cto_guidance"),
        error=data.get("error"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreateRequest,
    sm: StateMachine = Depends(get_state_machine),
    redis: aioredis.Redis = Depends(get_redis),
) -> JobResponse:
    """Enqueue a new coding job and route it to the appropriate worker."""
    task_id = str(uuid.uuid4())
    route = _task_router.route(body.description, explicit_role=body.role)

    metadata: dict[str, Any] = {
        "role": route.role.value,
        "queue": route.queue,
        "description": body.description,
        "pii_policy": body.pii_policy,
    }
    if body.tier:
        metadata["tier"] = body.tier
    if body.jira_ticket:
        metadata["jira_ticket"] = body.jira_ticket
    if body.context:
        metadata["context"] = body.context

    await sm.create(task_id, metadata)

    # Push task_id to role-specific Redis queue
    await redis.rpush(route.queue, task_id)

    data = await sm.get(task_id)
    return _serialize_job(data or {})


@router.get("/jobs/{task_id}", response_model=JobResponse)
async def get_job(
    task_id: str,
    sm: StateMachine = Depends(get_state_machine),
) -> JobResponse:
    data = await sm.get(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Job {task_id!r} not found")
    return _serialize_job(data)


@router.post("/jobs/{task_id}/approve", response_model=JobResponse)
async def approve_job(
    task_id: str,
    body: ApprovalRequest,
    sm: StateMachine = Depends(get_state_machine),
) -> JobResponse:
    """Human reviewer approves a PR — transitions REVIEW → APPROVED → DONE."""
    try:
        await sm.transition(
            task_id,
            TaskState.APPROVED,
            extra={"reviewer": body.reviewer, "review_comment": body.comment or ""},
        )
        await sm.transition(task_id, TaskState.DONE)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Feedback loop: persist positive example + auto-ingest patch into KB
    try:
        await _collector().on_approved(task_id)
    except Exception:
        pass  # never block approval on learning pipeline failure

    data = await sm.get(task_id)
    return _serialize_job(data or {})


@router.post("/jobs/{task_id}/reject", response_model=JobResponse)
async def reject_job(
    task_id: str,
    body: ApprovalRequest,
    sm: StateMachine = Depends(get_state_machine),
    redis: aioredis.Redis = Depends(get_redis),
) -> JobResponse:
    """Human reviewer rejects a PR — transitions REVIEW → REJECTED → PENDING and re-queues."""
    try:
        data_before = await sm.get(task_id)
        if data_before is None:
            raise HTTPException(status_code=404, detail=f"Job {task_id!r} not found")
        await sm.transition(
            task_id,
            TaskState.REJECTED,
            extra={"reviewer": body.reviewer, "reject_reason": body.comment or ""},
        )
        await sm.transition(task_id, TaskState.PENDING)
        # Re-enqueue onto the same role queue
        queue = data_before.get("queue", "")
        if queue:
            await redis.rpush(queue, task_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Feedback loop: persist negative example
    try:
        await _collector().on_rejected(task_id, body.comment or "")
    except Exception:
        pass  # never block rejection on learning pipeline failure

    data = await sm.get(task_id)
    return _serialize_job(data or {})


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    limit: int = 50,
    redis: aioredis.Redis = Depends(get_redis),
) -> list[JobResponse]:
    """Return recent jobs (scans forgechain:task:* keys)."""
    keys = []
    async for key in redis.scan_iter("forgechain:task:*", count=200):
        keys.append(key)
        if len(keys) >= limit:
            break

    jobs = []
    for key in keys:
        data = await redis.hgetall(key)
        if data:
            jobs.append(_serialize_job(data))

    jobs.sort(key=lambda j: j.updated_at, reverse=True)
    return jobs[:limit]


# ---------------------------------------------------------------------------
# Cost / ledger endpoints
# ---------------------------------------------------------------------------

@router.get("/cost/{task_id}", response_model=TaskCostResponse)
async def get_task_cost(
    task_id: str,
    ledger: TokenLedger = Depends(get_ledger),
) -> TaskCostResponse:
    """Return token consumption and USD cost breakdown for a single job."""
    summary = await ledger.get_task_summary(task_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"No ledger data for task {task_id!r}")
    return TaskCostResponse(**summary)


@router.get("/cost", response_model=GlobalCostResponse)
async def get_global_cost(
    ledger: TokenLedger = Depends(get_ledger),
) -> GlobalCostResponse:
    """Return running totals (tokens + cost) per provider across all jobs."""
    totals = await ledger.get_global_totals()
    # Attach tier→provider→model reference pricing so the UI can show rate cards
    tier_pricing = {
        tier: {
            "provider": provider,
            "model": model,
            "pricing_url": "see packages/providers/pricing.py",
        }
        for tier, (provider, model) in TIER_DEFAULTS.items()
    }
    return GlobalCostResponse(totals=totals, tier_pricing=tier_pricing)


@router.post("/jobs/{task_id}/cto-review", response_model=JobResponse)
async def request_cto_review(
    task_id: str,
    sm: StateMachine = Depends(get_state_machine),
    redis: aioredis.Redis = Depends(get_redis),
) -> JobResponse:
    """Manually trigger CTO (Claude) review for a job already in REVIEW state.

    This re-queues the job with cto_review=True so the worker runs the
    CTOReviewSignature pass over the existing Senior-produced patch.
    """
    data = await sm.get(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Job {task_id!r} not found")
    if data.get("state") != "review":
        raise HTTPException(status_code=400, detail="Job must be in 'review' state to request CTO review")

    queue = data.get("queue", "")
    if not queue:
        raise HTTPException(status_code=400, detail="Job has no queue — cannot re-enqueue")

    # Tag the task so the worker knows to start at CTO tier
    await redis.hset(f"forgechain:task:{task_id}", "tier", "cto")
    await sm.transition(task_id, TaskState.REJECTED)   # REVIEW → REJECTED
    await sm.transition(task_id, TaskState.PENDING)    # REJECTED → PENDING
    await redis.rpush(queue, task_id)

    data = await sm.get(task_id)
    return _serialize_job(data or {})


# ---------------------------------------------------------------------------
# Quant Layer — knowledge gaps + worker health
# ---------------------------------------------------------------------------

@router.get("/knowledge/gaps")
async def get_knowledge_gaps(
    min_queries: int = 5,
) -> dict:
    """Surface roles with low average retrieval scores — these need more KB ingestion.

    A role below the coverage threshold (default 0.25) means agents working on
    that role are generating patches without relevant documentation context.

    Returns roles sorted by worst coverage first with ingestion recommendations.
    """
    tracker = CoverageTracker(_redis_url())
    gaps = await tracker.get_gaps(min_queries=min_queries)
    all_scores = await tracker.get_all_scores()
    return {
        "gaps": gaps,
        "all_scores": all_scores,
        "threshold": float(os.getenv("FORGECHAIN_COVERAGE_GAP_THRESHOLD", "0.25")),
    }


@router.get("/health/workers")
async def get_worker_health() -> dict:
    """Return EMA quality scores and degradation alerts per role.

    A role is flagged as degraded when its approval rate EMA drops below
    0.6 for 7 or more consecutive days. Degraded roles receive a recommendation
    to run GET /forgechain/knowledge/gaps.

    Also returns current bandit stats (α/β counts per role/tier) when
    FORGECHAIN_USE_BANDIT=1.
    """
    ema = EMATracker(_redis_url())
    health = await ema.get_all_health()

    degraded = [r for r, h in health.items() if h["degraded"]]

    response: dict = {
        "workers": health,
        "degraded_roles": degraded,
        "alert": (
            f"{len(degraded)} role(s) degraded: {', '.join(degraded)}. "
            "Check GET /forgechain/knowledge/gaps."
        ) if degraded else None,
    }

    if os.getenv("FORGECHAIN_USE_BANDIT", "0") == "1":
        bandit = BanditRouter(_redis_url())
        response["bandit_stats"] = bandit.get_stats()

    return response
