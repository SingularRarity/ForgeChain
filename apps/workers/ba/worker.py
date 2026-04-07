"""Business Analyst agent worker — JIRA ticket review and requirements."""

import sys, os
sys.path.insert(0, "/app")

from typing import Any
from celery import Celery
from base_worker import BaseWorker

app = Celery("forgechain-ba", broker=os.environ["CELERY_BROKER_URL"], backend=None)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)


class BAWorker(BaseWorker):
    role = "ba"

    @property
    def system_prompt(self) -> str:
        return (
            "You are a business analyst reviewing JIRA tickets and engineering specifications.\n"
            "For each ticket: identify ambiguities, define acceptance criteria, suggest edge cases "
            "the engineering team should cover, and flag any missing non-functional requirements.\n"
            "Output a structured Markdown report with sections: "
            "Summary | Acceptance Criteria | Edge Cases | Open Questions | Risks."
        )

    def build_user_prompt(self, task: dict[str, Any]) -> str:
        ticket = task.get("jira_ticket", "")
        description = task.get("description", "")
        return (
            f"JIRA Ticket:\n{ticket}\n\n"
            f"Additional context:\n{description}"
        ) if ticket else super().build_user_prompt(task)


_worker = BAWorker()


@app.task(name="forgechain_ba_task")
def forgechain_ba_task(task_id: str) -> None:
    _worker.process(task_id)
