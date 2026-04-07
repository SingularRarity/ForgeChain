# ForgeChain Skills Library

Drop `.md` files here. Each file teaches one or more agent roles what
conventions, patterns, and APIs to follow in your specific codebase.

## How it works

1. You add a skills file (or point to a docs URL)
2. The ingester chunks it, embeds it via `nomic-embed-text` (Ollama, free)
3. Chunks are stored in ChromaDB under the role's collection
4. At inference time, the worker retrieves the 5 most relevant chunks
   for the current task and injects them into the prompt

**The model never retrains.** Knowledge lives in the vector store and
is retrieved at runtime. Add new skills any time — no rebuild needed.

## Ingest a file

```bash
# Single file → one role
python -m knowledge.cli ingest file --role backend_dev skills/backend_dev.md

# All files in this directory → one role
python -m knowledge.cli ingest dir --role backend_dev skills/

# A documentation URL
python -m knowledge.cli ingest url --role backend_dev https://fastapi.tiangolo.com/tutorial/

# Check what's been ingested
python -m knowledge.cli status
```

## File naming convention

| File | Role |
|------|------|
| `backend_dev.md`  | backend_dev |
| `frontend_dev.md` | frontend_dev |
| `qa_backend.md`   | qa_backend |
| `db_eng.md`       | db_eng |
| `ai_eng.md`       | ai_eng |
| `sre.md`          | sre |
| `ba.md`           | ba |
| `shared.md`       | ingest into all roles |

## What makes a good skills file

- **Concrete patterns**, not abstract principles. Show code snippets.
- **Your conventions**, not generic best practices the model already knows.
- **Error handling recipes** specific to your stack.
- **Library versions** you're pinned to (e.g. "we use Pydantic v2, not v1").
- **Anti-patterns to avoid** — things the model tends to do wrong in your codebase.
- **Real examples** from your codebase (stripped of PII).

## Recommended documentation URLs to ingest

### backend_dev
- https://fastapi.tiangolo.com/tutorial/
- https://docs.pydantic.dev/latest/
- https://docs.sqlalchemy.org/en/20/

### frontend_dev
- https://react.dev/learn
- https://tailwindcss.com/docs/

### db_eng
- https://alembic.sqlalchemy.org/en/latest/tutorial.html
- https://www.postgresql.org/docs/current/index.html

### qa_backend
- https://docs.pytest.org/en/stable/
- https://www.encode.io/httpx/

### sre
- https://docs.docker.com/reference/dockerfile/
- https://docs.github.com/en/actions/writing-workflows
