"""Backend Developer agent worker."""

import sys, os
sys.path.insert(0, "/app")

from celery import Celery
from base_worker import BaseWorker

app = Celery("forgechain-backend-dev", broker=os.environ["CELERY_BROKER_URL"], backend=None)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)


class BackendDevWorker(BaseWorker):
    role = "backend_dev"

    @property
    def system_prompt(self) -> str:
        return (
            "You are a senior backend engineer specialising in Python, FastAPI, and async patterns.\n"
            "Produce a production-ready code patch in unified diff format.\n"
            "Apply strict typing (Pydantic v2, mypy-clean).  Handle errors explicitly at every layer.\n"
            "Never expose internal stack traces to API clients. Use parameterised queries — no raw SQL."
        )


_worker = BackendDevWorker()


@app.task(name="forgechain_backend_dev_task")
def forgechain_backend_dev_task(task_id: str) -> None:
    _worker.process(task_id)
