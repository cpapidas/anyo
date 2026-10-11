"""Run a protected dashboard and a bounded local pipeline, without model calls."""

import logging
import os
import time

import anyo
from anyo import HookEvent
from anyo.execution import OperationsSettings


def report_pull_request(event: HookEvent) -> None:
    """Ten seconds of observable local work, suitable for trying the controls."""
    # Spawned processes start fresh: configure this consumer logger here instead
    # of relying on logging configuration performed only in the parent's main().
    logger = logging.getLogger("anyo.example.operations")
    logger.setLevel(logging.INFO)
    logger.info("Reviewing PR #%s", event.pull_request_number)
    for step in range(1, 11):
        print(f"PR #{event.pull_request_number}: local step {step}/10", flush=True)
        time.sleep(1)
    logger.info("Local demonstration completed")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    role = os.environ.get("ANYO_ROLE", "all")
    anyo.add_hook(
        provider="github",
        action="pr_created",
        organization=os.environ["ANYO_GITHUB_ORGANIZATION"],
        repository=os.environ["ANYO_GITHUB_REPOSITORY"],
        pipeline=report_pull_request,
        hook_id="operations-demo",
    )
    # Only the receiver serves HTTP and needs UI/webhook credentials. Every
    # worker enables operations against the same queue namespace and settings.
    anyo.start(
        role=role,
        ui=role != "worker",
        operations=OperationsSettings(),
        execution_mode=os.environ.get("ANYO_EXECUTION_MODE", "process"),
        queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/operations.sqlite3"),
        queue_namespace=os.environ.get("ANYO_QUEUE_NAMESPACE", "operations-demo"),
        host=os.environ.get("ANYO_HOST", "127.0.0.1"),
        port=int(os.environ.get("ANYO_PORT", "8000")),
    )


# Required for process execution: importing this module must not start a server
# or recursively create worker processes.
if __name__ == "__main__":
    main()
