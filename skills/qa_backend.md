# QA Backend Skills
## Applied to role: qa_backend

---

## Stack

- pytest 8+, pytest-asyncio, pytest-cov
- httpx.AsyncClient for FastAPI endpoint testing
- respx for mocking external HTTP calls
- factory_boy or pytest fixtures for test data
- Coverage target: ≥80% — enforce with `--cov-fail-under=80`

---

## Test structure

```
tests/
  unit/          Pure function tests — no I/O
  integration/   API endpoint tests with real Redis (test container)
  conftest.py    Shared fixtures
```

---

## FastAPI endpoint testing pattern

```python
import pytest
from httpx import AsyncClient, ASGITransport
from src.api.main import app

@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

@pytest.mark.asyncio
async def test_create_job_happy_path(client: AsyncClient):
    res = await client.post("/forgechain/jobs", json={
        "description": "Add a health check endpoint that returns 200",
        "role": "backend_dev",
    })
    assert res.status_code == 201
    data = res.json()
    assert data["state"] == "pending"
    assert data["role"] == "backend_dev"
    assert "task_id" in data
```

---

## Mocking external HTTP with respx

```python
import respx
import httpx

@pytest.mark.asyncio
async def test_llm_call_retries_on_timeout(client: AsyncClient):
    with respx.mock:
        respx.post("http://localhost:11434/api/embed").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        res = await client.post("/forgechain/jobs", json={"description": "test task"})
        # Should degrade gracefully, not 500
        assert res.status_code in (201, 503)
```

---

## Redis fixture (real Redis via testcontainers)

```python
import pytest
from testcontainers.redis import RedisContainer

@pytest.fixture(scope="session")
def redis_url():
    with RedisContainer("redis:7-alpine") as redis:
        yield redis.get_connection_url()
```

---

## Coverage configuration (pyproject.toml)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "--cov=src --cov=apps --cov-report=term-missing --cov-fail-under=80"

[tool.coverage.run]
omit = ["*/tests/*", "*/migrations/*", "*/__init__.py"]
```

---

## Test naming conventions

```python
# Pattern: test_{what}_{condition}_{expected_result}
async def test_create_job_missing_description_returns_422(): ...
async def test_approve_job_not_in_review_state_returns_400(): ...
async def test_state_machine_illegal_transition_raises_value_error(): ...
```

---

## What to avoid

- Testing implementation details — test behaviour through the public API
- Mocking the database — use real Redis in a test container
- `time.sleep()` in tests — use `asyncio.sleep()` or mock the clock
- Asserting only status codes — always assert response body shape too
- Giant test functions — one assertion cluster per test
