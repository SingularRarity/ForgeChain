"""Task router: maps a job description to the correct agent role and Redis queue."""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class AgentRole(str, Enum):
    FRONTEND_DEV = "frontend_dev"
    BACKEND_DEV = "backend_dev"
    QA_BACKEND = "qa_backend"
    DB_ENG = "db_eng"
    AI_ENG = "ai_eng"
    SRE = "sre"
    BA = "ba"
    SOLIDITY_DEV = "solidity_dev"


class RouteResult(NamedTuple):
    role: AgentRole
    queue: str       # Redis queue name
    celery_task: str # Celery task name to dispatch


# Keyword → role heuristics (cheapest routing; replace with an LLM classifier
# or explicit task metadata for production).
_KEYWORD_MAP: list[tuple[list[str], AgentRole]] = [
    # Solidity first — highest specificity, must not fall through to backend_dev
    (["solidity", "sol", "contract", "evm", "web3", "abi", "hardhat", "foundry",
      "erc20", "erc721", "erc1155", "reentrancy", "escrow", "on-chain", "onchain",
      "blockchain", "carbon credit", "pledge", "dao", "governance", "smart contract",
      "wei", "gwei", "payable", "emit", "modifier", "mapping("], AgentRole.SOLIDITY_DEV),
    (["react", "css", "tsx", "html", "ui", "frontend", "component", "tailwind"], AgentRole.FRONTEND_DEV),
    (["fastapi", "django", "flask", "api", "endpoint", "backend", "python"], AgentRole.BACKEND_DEV),
    (["test", "pytest", "coverage", "qa", "unit test", "integration test"], AgentRole.QA_BACKEND),
    (["migration", "schema", "sql", "postgres", "alembic", "database", "db"], AgentRole.DB_ENG),
    (["model", "train", "inference", "stt", "tts", "embedding", "ai", "ml"], AgentRole.AI_ENG),
    (["docker", "aws", "terraform", "cicd", "deploy", "k8s", "infra", "devops"], AgentRole.SRE),
    (["jira", "ticket", "requirement", "acceptance", "story", "business"], AgentRole.BA),
]

_ROLE_QUEUE = {role: f"forgechain:{role.value}" for role in AgentRole}
_ROLE_TASK = {role: f"forgechain_{role.value}_task" for role in AgentRole}


class TaskRouter:
    """Route a plain-text job description to a specific agent role."""

    def route(self, description: str, *, explicit_role: str | None = None) -> RouteResult:
        if explicit_role:
            role = AgentRole(explicit_role)
        else:
            role = self._infer_role(description)
        return RouteResult(
            role=role,
            queue=_ROLE_QUEUE[role],
            celery_task=_ROLE_TASK[role],
        )

    @staticmethod
    def _infer_role(description: str) -> AgentRole:
        lower = description.lower()
        scores: dict[AgentRole, int] = {role: 0 for role in AgentRole}
        for keywords, role in _KEYWORD_MAP:
            for kw in keywords:
                if kw in lower:
                    scores[role] += 1
        best = max(scores, key=lambda r: scores[r])
        # Default to backend_dev when nothing matches
        return best if scores[best] > 0 else AgentRole.BACKEND_DEV
