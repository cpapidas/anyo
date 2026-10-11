"""Use the same GitHub pipeline with SQLite, Redis, or separate workers."""

import os

import anyo
from anyo import HookEvent


def report_pull_request(event: HookEvent) -> None:
    """Replace this bounded, local operation with your pipeline."""
    print(
        f"PR {event.organization}/{event.repository}#{event.pull_request_number} "
        f"at {event.head_sha}; delivery={event.delivery_id}",
        flush=True,
    )


def main() -> None:
    # Every role executes the same registrations. Workers need no signing secret.
    anyo.add_hook(
        provider="github",
        action="pr_created",
        organization=os.environ["ANYO_GITHUB_ORGANIZATION"],
        repository=os.environ["ANYO_GITHUB_REPOSITORY"],
        pipeline=report_pull_request,
        # (optional) this is the id of the hook, it is used to identify the hook
        # in the database
        hook_id="report-pull-request",
    )
    # These environment variables belong to this example, not library discovery.
    anyo.start(
        queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/queue.sqlite3"),
        queue_namespace=os.environ.get("ANYO_QUEUE_NAMESPACE", "github-reviews"),
        role=os.environ.get("ANYO_ROLE", "all"),
        host=os.environ.get("ANYO_HOST", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
