# ForgeChain Shared Conventions
## Applied to: all roles

---

## Project overview

ForgeChain is a self-hosted AI coding agent fleet built on FastAPI + Redis +
Celery. Agents receive coding tasks, produce unified-diff patches, open draft
GitHub PRs, and wait for human approval before anything merges.

---

## Repository structure

```
apps/
  api/              FastAPI route extensions
  workers/          Celery agent workers (one per role)
  console/          React dashboard components
packages/
  providers/        LLM adapters + tiered router + token ledger
  orchestrator/     Task routing + Redis state machine
  policy/           PII vault (Fernet encryption)
  redaction/        PII detection before prompts
  dspy_prompts/     DSPy signatures + optimizer
  knowledge/        RAG pipeline (ChromaDB + nomic-embed-text)
src/
  api/              Original AsyncTaskFlow FastAPI app
  worker/           Original Celery worker
frontend/           React/Tailwind monitoring dashboard
infra/              Docker Compose, Dockerfiles, Postgres, OTEL
skills/             This directory — knowledge base source files
```

---

## Python conventions

- Python 3.12+, type annotations on all function signatures
- `from __future__ import annotations` at top of every file
- Pydantic v2 for all data models and validation
- `async`/`await` throughout — no blocking I/O in async context
- Immutable data: use `dataclass(frozen=True)` or `NamedTuple`; never mutate dicts in place
- Error handling: explicit at every layer; never silently swallow exceptions
- Logging: `logger = logging.getLogger(__name__)` — never `print()`
- No hardcoded secrets — all from `os.environ["KEY"]` (raises KeyError if missing)

## Output format for code patches

All agents produce **unified diff format**:

```diff
--- a/src/api/routers/tasks.py
+++ b/src/api/routers/tasks.py
@@ -10,6 +10,8 @@
 from fastapi import APIRouter
+from fastapi import HTTPException
+from pydantic import BaseModel
```

- File paths always relative to repo root
- Never include unrelated context lines
- One logical change per patch

## Git conventions

- Branch names: `forgechain/{role}/{task_id_prefix}`
- Commit format: `type(scope): description` — types: feat, fix, refactor, docs, test, chore
- PRs are always opened as drafts — never auto-merge

## Security rules (non-negotiable)

- No raw PII in any queue, log, or prompt
- All secrets from environment variables
- Parameterised queries only — never f-string SQL
- GitHub token scoped to repo read + PR create only
