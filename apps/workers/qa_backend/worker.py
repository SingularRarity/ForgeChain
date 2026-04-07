"""QA Backend agent worker — generates pytest suites."""

import sys, os
sys.path.insert(0, "/app")

from celery import Celery
from base_worker import BaseWorker

app = Celery("forgechain-qa-backend", broker=os.environ["CELERY_BROKER_URL"], backend=None)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)


class QABackendWorker(BaseWorker):
    role = "qa_backend"

    @property
    def system_prompt(self) -> str:
        return (
            "You are a QA engineer writing pytest test suites for Python/FastAPI services.\n"
            "Follow TDD: write tests first, then describe what implementation is needed.\n"
            "Cover: happy path, edge cases, error conditions, security boundaries.\n"
            "Use httpx.AsyncClient for API tests. Mock external services with respx.\n"
            "Aim for ≥80% coverage.  Output only the test file(s) in unified diff format."
        )


_worker = QABackendWorker()


@app.task(name="forgechain_qa_backend_task")
def forgechain_qa_backend_task(task_id: str) -> None:
    _worker.process(task_id)
