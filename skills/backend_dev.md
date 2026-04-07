# Backend Developer Skills
## Applied to role: backend_dev

---

## Stack

- Python 3.12, FastAPI 0.115+, Pydantic v2
- Celery 5.4 + Redis 7 (broker and custom task store)
- SQLAlchemy 2.0 (async) + Alembic for migrations
- httpx for async HTTP clients
- pytest + httpx.AsyncClient for tests

---

## FastAPI patterns

### Router structure

```python
# Each domain gets its own router file under src/api/routers/
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

router = APIRouter(prefix="/tasks", tags=["tasks"])

class TaskResponse(BaseModel):
    task_id: str
    state: str

@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    ...
```

### Dependency injection for services

```python
# services.py holds module-level singletons set at lifespan
import services

def get_task_service() -> TaskService:
    svc = services.task_service
    if svc is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    return svc
```

### Error handling — never expose internals

```python
# WRONG
raise HTTPException(status_code=500, detail=str(e))

# CORRECT
logger.exception("Task fetch failed for %s", task_id)
raise HTTPException(status_code=500, detail="Internal error retrieving task")
```

---

## Pydantic v2 models

```python
from pydantic import BaseModel, Field, model_validator
from typing import Optional

class JobCreateRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=4000)
    role: Optional[str] = None

    @model_validator(mode="after")
    def validate_role(self) -> "JobCreateRequest":
        valid_roles = {"frontend_dev", "backend_dev", "qa_backend", "db_eng", "ai_eng", "sre", "ba"}
        if self.role and self.role not in valid_roles:
            raise ValueError(f"role must be one of {valid_roles}")
        return self
```

---

## Async Redis patterns

```python
import redis.asyncio as aioredis

# Always use pipeline for multi-key writes
async def store_task(redis: aioredis.Redis, task_id: str, data: dict) -> None:
    pipe = redis.pipeline()
    pipe.hset(f"task:{task_id}", mapping=data)
    pipe.expire(f"task:{task_id}", 86400)
    await pipe.execute()

# Scan instead of KEYS for large keyspaces
async def list_tasks(redis: aioredis.Redis) -> list[str]:
    keys = []
    async for key in redis.scan_iter("task:*", count=100):
        keys.append(key)
    return keys
```

---

## Celery task patterns

```python
from celery import Task

class BaseTask(Task):
    abstract = True
    max_retries = 3

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("Task %s failed permanently: %s", task_id, exc)

@app.task(base=BaseTask, bind=True, name="process_job")
def process_job(self, task_id: str) -> None:
    try:
        ...
    except TemporaryError as e:
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
```

---

## What to avoid

- `await asyncio.sleep()` inside request handlers — use background tasks
- Returning raw SQLAlchemy ORM objects from endpoints — always serialize to Pydantic
- `SELECT *` queries — always name columns explicitly
- `time.sleep()` in async code — use `asyncio.sleep()`
- Module-level database connections — use dependency injection
