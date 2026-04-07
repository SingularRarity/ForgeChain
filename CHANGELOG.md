# Changelog

All notable changes to ForgeChain are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [0.8.0] — 2026-04-08 — Feedback Loop (Phase 2)

### Added
- `packages/learning/` — full feedback pipeline:
  - `collector.py` — `Collector.on_approved()` / `on_rejected()`: reads task
    metadata from Redis, builds a typed `Example`, persists it, and (on
    approval) triggers auto-ingestion of the patch into ChromaDB.
  - `example_store.py` — `ExampleStore`: dual-writes examples to a Redis list
    `forgechain:examples:{role}_{tier}` (fast trainer reads, last 500 per pair,
    30-day TTL) and Postgres `fc_examples` (durable audit trail). `get_examples()`
    filters by outcome for trainer consumption.
  - `trainer.py` — `Trainer.run_all()` / `run_one()`: loads approved examples,
    converts to `dspy.Example` objects, runs `BootstrapFewShot` (no LLM calls —
    fast nightly execution), saves compiled weights to
    `$FORGECHAIN_WEIGHTS_DIR/{role}_{tier}.json`. Skips (role, tier) pairs with
    fewer than `MIN_EXAMPLES` (default 10). Records last-run timestamp in Redis.
  - `auto_ingest.py` — `AutoIngestor.ingest()`: formats approved patch as a
    markdown document, chunks via existing `chunk_text()`, embeds via Ollama
    `nomic-embed-text`, upserts into the role's ChromaDB collection. Source key
    `patch:{task_id}` allows targeted deletion if a patch is later invalidated.
- `apps/workers/feedback_worker.py` — Celery beat task `forgechain_feedback_train`
  scheduled at 02:00 UTC nightly. Also runnable standalone:
  `python feedback_worker.py`. Logs trained/skipped summary per run.
- `infra/init.sql` — `fc_examples` table with indexes on `(role, tier)`,
  `outcome`, and `recorded_at`.

### Changed
- `apps/api/forgechain_router.py` — `approve_job` now calls
  `Collector.on_approved()` after state transitions. `reject_job` calls
  `Collector.on_rejected()`. Both are fire-and-forget (exceptions never block
  the human reviewer's response).

### Why
Workers already hot-load weights from `$FORGECHAIN_WEIGHTS_DIR/{role}_{tier}.json`
on every task (`modules.py`). The feedback loop writes to that path nightly,
so every approved PR makes the next similar task more likely to be approved
on the first attempt — without any manual prompt tuning.

---

## [0.7.0] — 2026-04-07 — PRD Engine (Phase 1)

### Added
- `packages/prd/` — PRD parsing and execution engine:
  - `models.py` — `PRDTask`, `ExecutionWave`, `TaskGraph` frozen dataclasses.
    All immutable — mutation returns new instances. `TaskGraph.as_dict()` for
    Redis serialisation. `task_by_id()` for O(n) lookup.
  - `parser.py` — `PRDParser`: calls Gemini (or best available provider) with
    a strict JSON-only system prompt. Extracts tasks with role, dependencies,
    and complexity. Validates all dependency edges against real task IDs.
    Falls back to a single-task plan if LLM output is unparseable.
  - `graph.py` — `build_graph()`: Kahn's topological sort assigns wave numbers
    (O(V+E)). Dynamic programming on the DAG computes the critical path (longest
    dependency chain). Returns a fully assembled `TaskGraph` with wave groupings.
  - `executor.py` — `WaveExecutor`: executes waves sequentially, jobs within
    each wave in parallel. Creates ForgeChain jobs via the existing state machine
    and Redis queue. Polls every 10s (30-minute timeout per wave). Writes live
    progress to `forgechain:prd:{id}` in Redis.
- `apps/api/prd_router.py` — four new routes under `/forgechain`:
  - `POST /forgechain/prd` — parse PRD text, return full plan before any code
    is written (LLM call; 5–30s).
  - `POST /forgechain/prd/{id}/execute` — start background wave execution;
    returns immediately, workers process tasks asynchronously.
  - `GET /forgechain/prd/{id}` — poll state, current wave, per-task job IDs.
  - `GET /forgechain/prd/{id}/graph` — nodes + directed edges for UI graph
    rendering; each node annotated with wave number and `is_critical` flag.
- `src/api/main.py` — PRD router registered alongside forgechain router
  (opt-in, graceful import fallback).

### Why
PRD volume is the prerequisite for Phase 2 (feedback loop) and Phase 3 (quant
optimisation). Without a stream of automatically generated tasks there is no
training signal for DSPy or the Thompson Sampling bandit. This phase generates
that volume automatically.

---

## [0.6.0] — 2026-04-07 — RAG knowledge base for local Ollama enrichment

### Added
- `packages/knowledge/` — full RAG pipeline:
  - `chunker.py` — Markdown-aware text splitter (heading-preserving, sliding
    window, configurable max_chars/overlap to fit nomic-embed-text's 2048
    token context window).
  - `embedder.py` — calls Ollama `/api/embed` with `nomic-embed-text`; local,
    free, retries on transient failures.
  - `store.py` — `KnowledgeStore`: ChromaDB persistent collections, one per
    agent role. Cosine similarity, upsert-idempotent by content hash, 30-day TTL.
  - `retriever.py` — `Retriever.retrieve_as_context()`: embeds the task
    description, fetches top-5 chunks above 0.30 similarity, formats them
    as a citation block capped at 3000 chars (stays within Ollama budget).
  - `ingester.py` — `Ingester`: ingest local `.md`/`.txt`/`.rst` files,
    HTTP/HTTPS URLs (HTML stripped), or full directory trees.
  - `cli.py` — `python -m knowledge.cli` with `ingest file|url|dir`,
    `status`, `delete`, `list-roles` subcommands.
- `skills/` directory — knowledge base source files:
  - `README.md` — usage guide and recommended doc URLs per role.
  - `shared.md` — project conventions applied to all roles.
  - `backend_dev.md` — FastAPI, Pydantic v2, async Redis, Celery patterns.
  - `frontend_dev.md` — React 18, TypeScript, Tailwind, React Query, Zustand.
  - `qa_backend.md` — pytest-asyncio, httpx.AsyncClient, respx, testcontainers.
  - `db_eng.md` — Alembic migrations, SQLAlchemy 2.0 async, index strategy.
  - `sre.md` — Dockerfile conventions, docker-compose, GitHub Actions, secrets.

### Changed
- All DSPy signatures gain a `retrieved_knowledge` input field —
  `JuniorCodeSignature`, `MidCodeSignature`, `SeniorCodeSignature`,
  `BATicketSignature`, `QATestSignature`.
- `BaseWorker.build_inputs()` now calls `Retriever.retrieve_as_context()`
  on every task before building DSPy inputs. If the knowledge base is empty
  the field is an empty string and the model falls back to its own knowledge.
- `BaseWorker.__init__` instantiates a `Retriever` for the worker's role.
- `infra/ollama/setup_ollama.sh` now also pulls `nomic-embed-text`.
- `apps/workers/pyproject.toml` adds `chromadb>=0.5` and `httpx>=0.27`.
- `.env.example` adds `FORGECHAIN_EMBED_MODEL` and `FORGECHAIN_KB_PATH`.

### Rationale
Fine-tuning Ollama requires GPU hours, thousands of labeled examples, and
a full model rebuild each time knowledge changes — too expensive and too
slow for a living codebase. RAG achieves the same effect at zero marginal
cost: drop a `skills.md`, run one CLI command, and the knowledge is
immediately available at the next inference call. The embedding model
(`nomic-embed-text`) runs inside the same Ollama container already
required by the junior tier, so no new infrastructure is needed.

---

## [0.5.0] — 2026-04-07 — Ollama model optimisation

### Added
- `infra/ollama/Modelfile.forgechain-junior` — custom Ollama model built on
  `qwen2.5-coder:7b` with temperature 0.1, 8k context, repeat-penalty, and a
  code-specific system prompt baked into the weights.
- `infra/ollama/setup_ollama.sh` — one-shot script to pull the base model,
  register the Modelfile, and verify the `forgechain-junior` model is available.
- `qwen2.5-coder:7b` and `deepseek-coder-v2` added to the pricing table.

### Changed
- `JuniorCodeSignature` gains an explicit `reasoning` output field — forces the
  local model to emit a step-by-step plan before writing code, which
  significantly reduces hallucinated file paths and non-compilable diffs.
- `ForgeChainModule._build_module` now uses `ChainOfThought` at every tier
  (previously `Predict` for junior). Small models need the reasoning scaffold.

### Rationale
`llama3.2` is a general chat model. Swapping to a code-tuned model plus a
Modelfile that locks in low temperature is the single highest-impact change
for local inference quality. `ChainOfThought` gives the model a scratchpad so
it doesn't commit to a wrong answer on the first token.

---

## [0.4.0] — 2026-04-07 — Cost tracking API and CTO review trigger

### Added
- `GET /forgechain/cost/{task_id}` — token breakdown and USD cost per call for
  a single job.
- `GET /forgechain/cost` — running totals (tokens + cost) per provider across
  all jobs, plus a tier→model rate-card reference.
- `POST /forgechain/jobs/{id}/cto-review` — manually escalate any REVIEW-state
  job to Claude for CTO-level architectural review without re-running the full
  pipeline.
- `fc_token_ledger` Postgres table — append-only audit log with
  `entry_id, task_id, role, tier, stage, provider, model, tokens, cost`.
- `asyncpg` and `dspy-ai` added to `apps/workers/pyproject.toml`.

### Changed
- `JobResponse` schema extended with `tier_used`, `provider`, `model`,
  `cto_verdict`, `cto_guidance` so callers always know which model ran.
- `JobCreateRequest` accepts optional `tier` field to override the role default.

### Rationale
Without visible cost data, tiered routing is invisible to the operator. The
cost endpoints close that loop. The manual CTO trigger exists because not
every job that reaches REVIEW warrants Claude — the human decides.

---

## [0.3.0] — 2026-04-07 — DSPy prompt optimisation layer

### Added
- `packages/dspy_prompts/` — new package containing:
  - `signatures.py` — typed DSPy `Signature` classes per role × tier:
    `JuniorCodeSignature`, `MidCodeSignature`, `SeniorCodeSignature`,
    `CTOReviewSignature`, `BATicketSignature`, `QATestSignature`,
    `JuniorAnalysisSignature`.
  - `lm_config.py` — `configure_dspy_lm(tier)` maps each tier to its litellm
    model string and configures the DSPy global LM.
  - `modules.py` — `ForgeChainModule`: loads pre-compiled weights if available,
    falls back to `ChainOfThought(signature)`.
  - `optimizer.py` — offline CLI (`python -m dspy_prompts.optimizer`) using
    `MIPROv2` or `BootstrapFewShot` to compile few-shot examples into optimised
    prompt weights, saved to `$FORGECHAIN_WEIGHTS_DIR/{role}_{tier}.json`.

### Rationale
Raw string prompts degrade as the codebase evolves. DSPy signatures decouple
the *structure* of a prompt (what fields go in, what fields come out) from the
*wording* (what the model actually sees), and allow the optimizer to find
better instruction wordings automatically from real examples.

---

## [0.2.0] — 2026-04-07 — Tiered LLM routing and token ledger

### Added
- `packages/providers/pricing.py` — token pricing table (USD/1M tokens) for
  all four providers. Single file to update when rates change.
- `packages/providers/token_ledger.py` — `TokenLedger`: records every LLM call
  to Redis (immediate) and Postgres (async). Tracks tokens, cost, provider,
  tier, and stage per task.
- `packages/providers/tiered_router.py` — `TierRoute`, `get_route()`,
  `escalate()`: maps (role, tier) → (provider, model). Defines role baseline
  tiers and auto-escalation ceilings.

### Changed
- `apps/workers/base_worker.py` fully rewritten: now uses `ForgeChainModule`
  (DSPy) instead of raw `provider.complete()`. Runs at role's base tier,
  auto-escalates on `confidence=low`, triggers CTO review when Senior sets
  `cto_flag=yes`. All calls recorded in `TokenLedger`.
- `infra/init.sql` extended with `fc_token_ledger` table.

### Tier mapping
| Tier | Provider | Model | Roles |
|------|----------|-------|-------|
| junior | Ollama | llama3.2 | ba, qa_backend |
| mid | Grok | grok-3-mini | frontend_dev, backend_dev, sre |
| senior | Gemini | gemini-1.5-pro | db_eng, ai_eng |
| cto | Anthropic | claude-sonnet-4-6 | explicit only |

### Rationale
A flat "always use Claude" strategy is expensive and slow. Ollama handles
junior tasks at $0. Grok handles mid tasks at ~$0.001/call. Gemini handles
senior tasks. Claude is reserved for CTO review — architecture, security, and
refactoring decisions — where its reasoning depth justifies the cost.

---

## [0.1.4] — 2026-04-07 — Production infrastructure

### Added
- `infra/docker-compose.prod.yml` — production stack: per-role worker services
  (scaled via `ROLE_REPLICAS` env vars), Postgres, OpenTelemetry collector,
  optional Ollama service.
- `infra/Dockerfile.worker` — shared worker image built from repo root context
  so it can see both `/apps` and `/packages`.
- `infra/init.sql` — Postgres bootstrap: `fc_job_audit` and `fc_patch` tables.
- `infra/otel-config.yaml` — OTEL collector with OTLP receivers, batch
  processor, Prometheus exporter, and console logging.

### Rationale
The base `docker-compose.yml` runs a single generic worker pool. ForgeChain
needs per-role queues with independent scaling — a busy sprint may need 5
backend_dev workers and 1 ba worker. Postgres provides durable audit history
that survives Redis flushes. OTEL makes latency and token cost visible to
whoever is on call.

---

## [0.1.3] — 2026-04-07 — React console extensions

### Added
- `apps/console/src/components/TaskCreateForm.tsx` — job intake form with role
  selector, PII policy toggle, JIRA ticket field, and context field.
- `apps/console/src/components/DiffViewer.tsx` — line-coloured unified diff
  viewer with line numbers; truncates at configurable max lines.
- `apps/console/src/components/PRApproval.tsx` — approve / reject panel that
  calls the API approval endpoints and embeds `DiffViewer`.

### Rationale
The existing dashboard shows task status but has no way to submit jobs or act
on review-state PRs. These three components close the full loop from intake
to approval without leaving the browser.

---

## [0.1.2] — 2026-04-07 — ForgeChain API routes

### Added
- `apps/api/forgechain_router.py`:
  - `POST /forgechain/jobs` — enqueue a job; auto-routes to role queue.
  - `GET  /forgechain/jobs/{id}` — fetch job state.
  - `GET  /forgechain/jobs` — list recent jobs.
  - `POST /forgechain/jobs/{id}/approve` — REVIEW → APPROVED → DONE.
  - `POST /forgechain/jobs/{id}/reject` — REVIEW → REJECTED → PENDING +
    re-enqueue.
- `src/api/main.py` updated to conditionally register the ForgeChain router;
  existing routes are untouched.

### Rationale
The existing API handles generic tasks routed through a single queue. ForgeChain
needs role-specific queues and a human approval gate — concepts that don't map
onto the existing router structure. A separate router keeps the two concerns
cleanly separated.

---

## [0.1.1] — 2026-04-07 — Role-specific agent workers

### Added
- `apps/workers/base_worker.py` — abstract base: PII redact → LLM call →
  store patch → open draft GitHub PR → transition to REVIEW. GitHub token
  scoped to read + PR create only; never merges.
- `apps/workers/{frontend_dev,backend_dev,qa_backend,db_eng,ai_eng,sre,ba}/worker.py`
  — seven role workers, each a thin subclass that sets `role` and
  `system_prompt`. Registered as named Celery tasks.
- `apps/workers/pyproject.toml` — shared Python dependencies for the worker
  image.

### Rationale
One generic worker cannot hold the context needed to do good work across every
role. A frontend engineer and a DBA need completely different system prompts,
toolsets, and output formats. Separate workers make each agent's behaviour
auditable and independently replaceable.

---

## [0.1.0] — 2026-04-07 — Core packages

### Added
- `packages/providers/` — LLM provider layer:
  - `base.py` — `BaseLLMProvider` ABC + `LLMResponse` dataclass.
  - Adapters: `AnthropicProvider`, `GeminiProvider`, `GrokProvider`,
    `OllamaProvider` (Grok and Ollama reuse the `openai` SDK via compatible
    base URLs).
  - `__init__.py` — `get_provider(name?)` factory with auto-selection priority.
- `packages/redaction/` — `Redactor.redact(text)`: masks emails, phones, SSNs,
  JWTs, credit cards, and 32+ char secrets before any text enters a prompt.
- `packages/policy/` — `PIIVault` (Fernet-encrypted in-memory store) +
  `PIIPolicy` (strict / permissive modes).
- `packages/orchestrator/` — `TaskRouter` (keyword-to-role heuristic) +
  `StateMachine` (Redis-backed, enforces valid state transitions:
  pending→running→review→approved→done).

### Rationale
All four packages are intentionally provider-agnostic and worker-agnostic.
Keeping LLM adapters, PII handling, and orchestration in `packages/` means
any worker or API route can import them without circular dependencies. The
`get_provider()` factory makes swapping models a one-line env change.

---

## [0.0.1] — 2026-04-07 — Bootstrap from fastapi-async

### Added
- Cloned [rjalexa/fastapi-async](https://github.com/rjalexa/fastapi-async) as
  the starting scaffold: FastAPI API, Redis queue, Celery worker pool, circuit
  breaker, rate limiter, React/Tailwind dashboard.
- Updated `README.md` with ForgeChain project description, architecture
  diagram, 5-step migration plan, file tree, and security checklist.
- Updated `.env.example` with ForgeChain-specific variables while preserving
  all original AsyncTaskFlow variables.

### Rationale
fastapi-async provides battle-tested Redis queue mechanics, circuit breakers,
worker health checks, and a live monitoring dashboard — exactly the
infrastructure ForgeChain needs. Building on it avoids re-implementing
distributed task plumbing from scratch.
