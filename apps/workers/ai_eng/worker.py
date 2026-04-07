"""AI Engineer agent worker — ML, STT/TTS, embeddings."""

import sys, os
sys.path.insert(0, "/app")

from celery import Celery
from base_worker import BaseWorker

app = Celery("forgechain-ai-eng", broker=os.environ["CELERY_BROKER_URL"], backend=None)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)


class AIEngWorker(BaseWorker):
    role = "ai_eng"

    @property
    def system_prompt(self) -> str:
        return (
            "You are an AI engineer working on production ML systems (PyTorch, HuggingFace, OpenAI).\n"
            "Tasks may include: model integration, STT/TTS pipelines, embedding search, fine-tuning scripts.\n"
            "Write clean, type-annotated Python.  Validate tensor shapes at model boundaries.\n"
            "Include cost estimates and latency budgets in your implementation notes.\n"
            "Output unified diff format."
        )


_worker = AIEngWorker()


@app.task(name="forgechain_ai_eng_task")
def forgechain_ai_eng_task(task_id: str) -> None:
    _worker.process(task_id)
