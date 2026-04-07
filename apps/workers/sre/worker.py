"""SRE agent worker — Docker, AWS, Terraform checks."""

import sys, os
sys.path.insert(0, "/app")

from celery import Celery
from base_worker import BaseWorker

app = Celery("forgechain-sre", broker=os.environ["CELERY_BROKER_URL"], backend=None)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True)


class SREWorker(BaseWorker):
    role = "sre"

    @property
    def system_prompt(self) -> str:
        return (
            "You are an SRE/DevOps engineer responsible for Docker, Kubernetes, AWS, and CI/CD pipelines.\n"
            "Tasks may include: Dockerfile optimisation, GitHub Actions workflows, Terraform modules, "
            "auto-scaling policies, alerting rules, security group audits.\n"
            "Prioritise: least-privilege IAM, no secrets in images, health checks on every service.\n"
            "Output unified diff format (YAML/HCL/shell as appropriate)."
        )


_worker = SREWorker()


@app.task(name="forgechain_sre_task")
def forgechain_sre_task(task_id: str) -> None:
    _worker.process(task_id)
