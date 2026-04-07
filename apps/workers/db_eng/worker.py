"""Database Engineer agent worker — schema design and migrations."""

import sys, os
sys.path.insert(0, "/app")

from celery import Celery
from base_worker import BaseWorker

app = Celery("forgechain-db-eng", broker=os.environ["CELERY_BROKER_URL"], backend=None)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)


class DBEngWorker(BaseWorker):
    role = "db_eng"

    @property
    def system_prompt(self) -> str:
        return (
            "You are a database engineer specialising in PostgreSQL and SQLAlchemy/Alembic.\n"
            "Produce migration scripts (Alembic `upgrade` + `downgrade`) and schema patches.\n"
            "Always: use parameterised queries, add appropriate indexes, include rollback logic.\n"
            "Never store plaintext secrets or PII in the database without encryption.\n"
            "Output unified diff format including the migration file and any model changes."
        )


_worker = DBEngWorker()


@app.task(name="forgechain_db_eng_task")
def forgechain_db_eng_task(task_id: str) -> None:
    _worker.process(task_id)
