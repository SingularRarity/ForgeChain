# SRE Skills
## Applied to role: sre

---

## Stack

- Docker + Docker Compose v2
- GitHub Actions for CI/CD
- Redis 7, Postgres 16, Ollama
- OpenTelemetry (traces + metrics → console + Prometheus)

---

## Dockerfile conventions

```dockerfile
# Always pin base image digest or tag
FROM python:3.12-slim AS base

# Non-root user — always
ARG UID=1000
RUN groupadd -g ${UID} app && useradd -u ${UID} -g app -m app

# Install deps before copying app code (layer cache)
COPY pyproject.toml ./
RUN pip install uv && uv sync --no-dev

COPY --chown=app:app . .
USER app

# Explicit healthcheck on every service image
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
```

---

## docker-compose.prod.yml patterns

```yaml
# Resource limits on every service — no unbounded containers
deploy:
  resources:
    limits:
      memory: 512M
      cpus: "1.0"

# Depend on health, not just started
depends_on:
  redis:
    condition: service_healthy

# Structured logging
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "5"
```

---

## GitHub Actions CI template

```yaml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install uv && uv sync
      - run: uv run pytest --cov --cov-fail-under=80
      - run: uv run ruff check .
```

---

## Secrets management

- Never in image layers — always `ENV` from compose or K8s secrets
- Rotate on breach — document rotation procedure in runbook
- FORGECHAIN_VAULT_KEY — generate fresh per environment:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

---

## What to avoid

- `latest` image tags in production — pin to digest or semver tag
- Exposing Redis/Postgres ports on `0.0.0.0` — bind to internal network only
- `privileged: true` containers — never needed for this stack
- Secrets in environment variables committed to git — use `.env` in `.gitignore`
- `restart: always` without resource limits — combine both
