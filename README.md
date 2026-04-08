# ForgeChain — AI Dev Team, Self-Hosted

ForgeChain is an async AI coding system that runs a fleet of role-specific LLM agents on your infrastructure. You submit a task or PRD; agents write code, open GitHub PRs, and wait for human approval before anything merges.

Built on [AsyncTaskFlow](https://github.com/rjalexa/fastapi-async) — see that repo's README for the underlying task queue, circuit breaker, and monitoring documentation.

---

## How it works

```
You (API / PRD file)
       │
       ▼
FastAPI  /forgechain/jobs  /forgechain/prd
       │
       ▼
Redis queues  forgechain:{role}
       │
       ▼
Role Workers  (Celery — autoscales per queue depth)
  frontend_dev · backend_dev · qa_backend
  db_eng · ai_eng · sre · ba
       │
       ├─── Ollama (junior/mid — free, local)
       └─── Anthropic / Gemini / Kilo (senior/CTO — API)
       │
       ▼
Unified-diff patch → GitHub PR
       │
       ▼
Human review gate  (approve / reject)
       │
       ▼
State: pending → running → review → approved → done
```

---

## What makes it different

| Feature | ForgeChain |
|---------|-----------|
| Async task queue | Celery + Redis — jobs survive restarts, retry on failure |
| Human review gate | Nothing merges without approval; full Postgres audit trail |
| Cost-tiered LLM routing | Ollama (free, local) for junior/mid → paid APIs only for senior decisions |
| Dynamic scaling | Two-layer: Celery autoscale within containers + queue-depth container scaler |
| Multi-project | One instance, many repos, per-project GitHub tokens stored in registry |
| PRD execution | Wave-based execution engine turns a PRD into parallelised jobs |
| Notifications | Telegram + Discord on key events (review ready, approved, rejected, etc.) |
| Sandbox validation | Optional: validates patches against target project's own Docker env before review |

---

## Quick start

```bash
git clone <this-repo> forgechain
cd forgechain
cp .env.example .env        # fill in API keys — see sections below
docker compose up -d --build
```

Services:
- **API + dashboard** → http://localhost:8000 / http://localhost:3000
- **API docs** → http://localhost:8000/docs

---

## LLM providers

Set `FORGECHAIN_LLM_PROVIDER` to one of: `kilo | anthropic | gemini | grok | ollama`

If unset, the first provider with an API key is chosen automatically (Kilo checked first).

### Tier routing

| Tier | Default provider | Cost |
|------|-----------------|------|
| Junior | Ollama (qwen2.5-coder) | $0 |
| Mid | Ollama (deepseek-coder-v2) | $0 |
| Senior | Anthropic / Gemini | API cost |
| CTO | Anthropic / Gemini | API cost |

**Remap tiers to Kilo free models** (reduces cost further):
```env
FORGECHAIN_JUNIOR_PROVIDER=kilo
FORGECHAIN_JUNIOR_MODEL=qwen/qwen3-coder
FORGECHAIN_MID_PROVIDER=kilo
FORGECHAIN_MID_MODEL=deepseek/deepseek-r1-0528
```

Free Kilo models: `qwen/qwen3-coder` · `deepseek/deepseek-r1-0528` · `moonshotai/kimi-k2` · `moonshotai/kimi-k2.5` · `minimax/minimax-m2`

### Kilo AI (free daily quota)
```env
KILO_API_KEY=your-key          # https://kilo.ai/profile
KILO_MODEL=qwen/qwen3-coder
```

### Anthropic
```env
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6
```

### Gemini
```env
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-1.5-pro
```

### Ollama (local — always recommended)
```env
OLLAMA_NUM_PARALLEL=2
OLLAMA_MAX_LOADED_MODELS=1     # 1 model in VRAM at a time
OLLAMA_KEEP_ALIVE=10m          # unload after 10min idle
```

After first start, pull models:
```bash
docker compose exec ollama bash infra/ollama/setup_ollama.sh
```

---

## Dynamic scaling

ForgeChain uses two scaling layers so idle containers don't burn RAM/CPU:

**Layer 1 — Celery autoscale** (within each container, responds in ~5s)
- Each role worker starts at `FC_WORKER_MIN_CONCURRENCY` processes, bursts to `FC_WORKER_MAX_CONCURRENCY`
- No new containers — just more Celery processes in the existing one

**Layer 2 — Queue-depth container scaler** (whole containers, sustained load)
- `fc-scaler` service polls Redis queue lengths every `FC_SCALER_INTERVAL` seconds
- Adds a container when queue depth ≥ `FC_SCALER_UP_THRESHOLD` and below per-role max
- Removes a container after `FC_SCALER_DOWN_COOLDOWN` seconds of empty queue
- Only ever removes containers it started — never touches the base replica

```env
FC_WORKER_MIN_CONCURRENCY=1
FC_WORKER_MAX_CONCURRENCY=4
FC_SCALER_INTERVAL=20
FC_SCALER_UP_THRESHOLD=2
FC_SCALER_DOWN_COOLDOWN=180
FC_SCALER_MAX_BACKEND_DEV=4
FC_SCALER_MAX_FRONTEND_DEV=3
FC_SCALER_MAX_QA_BACKEND=3
FC_SCALER_MAX_DB_ENG=2
FC_SCALER_MAX_AI_ENG=2
FC_SCALER_MAX_SRE=2
FC_SCALER_MAX_BA=2
```

---

## GitHub integration

**Global fallback** (single project):
```env
GITHUB_TOKEN=ghp_...
GITHUB_REPO=your-org/your-repo
```

**Per-project tokens** (multi-project — recommended):
```bash
# Set at registration time
curl -X POST http://localhost:8000/forgechain/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"my-app","github_token":"ghp_...","github_repo":"org/repo"}'

# Rotate later without re-registering
curl -X PATCH http://localhost:8000/forgechain/projects/{id}/github \
  -H "Content-Type: application/json" \
  -d '{"github_token":"ghp_new..."}'
```

Tokens are stored in Redis and never returned by the API. Required token scope: `repo` (read + PR create). Never grant merge or admin permissions.

---

## Notifications

**Telegram:**
```env
TELEGRAM_BOT_TOKEN=...   # from @BotFather
TELEGRAM_CHAT_ID=...     # from @userinfobot
```

**Discord:**
```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Events: `job_review` · `job_approved` · `job_rejected` · `prd_wave_done` · `prd_done` · `ema_degraded`

---

## Submitting jobs

**Single job:**
```bash
curl -X POST http://localhost:8000/forgechain/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Add a /ping endpoint that returns {\"ok\": true}",
    "role": "backend_dev",
    "project_id": "optional-project-id"
  }'
```

**PRD execution:**
```bash
curl -X POST http://localhost:8000/forgechain/prd \
  -H "Content-Type: application/json" \
  -d '{"prd": "...", "project_id": "my-app"}'
```

**Review a job:**
```bash
# Approve
curl -X POST http://localhost:8000/forgechain/jobs/{task_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"reviewer": "your-name", "comment": "LGTM"}'

# Reject
curl -X POST http://localhost:8000/forgechain/jobs/{task_id}/reject \
  -H "Content-Type: application/json" \
  -d '{"reviewer": "your-name", "comment": "needs rework"}'
```

---

## Multi-project registry

Register a project once; ForgeChain routes jobs, credentials, and knowledge base per project:

```bash
curl -X POST http://localhost:8000/forgechain/projects \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-app",
    "description": "My SaaS product",
    "github_token": "ghp_...",
    "github_repo": "org/my-app"
  }'
```

```env
FORGECHAIN_PROJECTS_PATH=/app/projects    # per-project KB root
FORGECHAIN_KB_PATH=/app/knowledge_base   # shared KB
FORGECHAIN_CROSS_PROJECT_SIMILARITY=0.75
FORGECHAIN_SHARED_ENTROPY_THRESHOLD=0.50
```

---

## Sandbox validation (optional)

Validates patches against the target project's own Docker environment before moving to review. Always non-blocking — failures attach a report to the job but never block review.

```env
FORGECHAIN_SANDBOX_ENABLED=1
FORGECHAIN_SANDBOX_SKIP_DOCKER=0
FORGECHAIN_SANDBOX_SKIP_BROWSER=0
FORGECHAIN_SANDBOX_SKIP_AB=0
```

Requires the worker to have Docker socket access (already configured in `docker-compose.yml`).

---

## File tree (ForgeChain additions)

```
apps/
├── api/
│   ├── forgechain_router.py    # Job submit, approve, reject, status
│   ├── prd_router.py           # PRD execution engine
│   └── registry_router.py      # Project registry CRUD + GitHub credential rotation
├── workers/
│   ├── base_worker.py          # LLM call, PII vault, GitHub PR, per-project creds
│   ├── frontend_dev/worker.py
│   ├── backend_dev/worker.py
│   ├── qa_backend/worker.py
│   ├── db_eng/worker.py
│   ├── ai_eng/worker.py
│   ├── sre/worker.py
│   └── ba/worker.py
└── scaler/
    └── scaler.py               # Queue-depth container scaler

packages/
├── providers/                  # LLM adapters: Kilo, Anthropic, Gemini, Grok, Ollama
│   ├── __init__.py             # Auto-detection + ProviderName registry
│   ├── tiered_router.py        # Junior/mid/senior/CTO tier routing
│   └── pricing.py              # Cost tracking per provider/model
├── orchestrator/               # TaskRouter + StateMachine
├── policy/                     # PIIPolicy + PIIVault (Fernet encryption)
├── registry/                   # ProjectRegistry (Redis-backed)
├── sandbox/                    # Patch validation against target project
└── redaction/                  # PII detection and masking

infra/
├── Dockerfile.worker           # Shared worker image
├── Dockerfile.scaler           # Queue-depth scaler image
├── init.sql                    # Postgres schema (audit log + patch storage)
└── ollama/
    └── setup_ollama.sh         # Pull junior/mid models into Ollama
```

---

## Security checklist

- [ ] All API keys in `.env` — never committed (`.gitignore` covers `.env`)
- [ ] `FORGECHAIN_VAULT_KEY` set — PII encrypted at rest with Fernet
- [ ] GitHub tokens scoped to `repo` read + PR create only — **no merge, no admin**
- [ ] Use fine-grained PATs per project — each token only touches its own repo
- [ ] Branch protection: require PR reviews + status checks
- [ ] Workers run as non-root (`UID=1000`) inside Docker
- [ ] No raw PII in Redis queues — redaction runs before every LLM prompt
- [ ] State machine enforces review gate — agents **cannot** auto-merge PRs
- [ ] Postgres audit log captures every state transition

---

## Based on AsyncTaskFlow

The underlying task queue, circuit breaker, Redis data structures, SSE monitoring, and distributed rate limiting are documented in the [AsyncTaskFlow repository](https://github.com/rjalexa/fastapi-async).
