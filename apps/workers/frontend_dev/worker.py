"""Frontend Developer agent worker."""

import sys
sys.path.insert(0, "/app")

from celery import Celery
import os

from base_worker import BaseWorker

app = Celery(
    "forgechain-frontend-dev",
    broker=os.environ["CELERY_BROKER_URL"],
    backend=None,
)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)


class FrontendDevWorker(BaseWorker):
    role = "frontend_dev"

    @property
    def system_prompt(self) -> str:
        return (
            "You are a senior frontend engineer specialising in React, TypeScript, and Tailwind CSS.\n"
            "Given a task description, produce a clean, production-ready code patch.\n"
            "Output ONLY the changed files in unified diff format, prefixed with the file path.\n"
            "Follow accessibility best practices (WCAG 2.1 AA). Use functional components and hooks.\n"
            "Never mutate state directly — use setState / reducers / Zustand immutably."
        )


_worker = FrontendDevWorker()


@app.task(name="forgechain_frontend_dev_task")
def forgechain_frontend_dev_task(task_id: str) -> None:
    _worker.process(task_id)
