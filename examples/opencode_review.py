"""Run a GitHub PR hook with an OpenCode harness in the same container."""

import json
import os
from pathlib import Path

import anyo
from anyo import HookEvent, PromptEvent

PROMPT = Path(__file__).with_name("review_pr.md")


def show_progress(event: PromptEvent) -> None:
    print(event.run_id, event.kind, event.message or "", flush=True)


def review_pull_request(event: HookEvent) -> None:
    result = anyo.inject_prompt(
        PROMPT,
        context={
            "organization": event.organization,
            "repository": event.repository,
            "pull_request_number": event.pull_request_number,
            "head_sha": event.head_sha,
        },
        workspace=os.environ.get("ANYO_WORKSPACE", "/workspace"),
        on_event=show_progress,
        timeout=300,
    )
    if result.success is not None:
        print(json.dumps(result.success.data))
    else:
        assert result.failure is not None
        print(result.failure.code, result.failure.message)
        result.raise_for_failure()


if __name__ == "__main__":
    anyo.harness(
        provider="opencode",
        plugins=[],
        models=[{"model": os.environ["ANYO_MODEL"], "key": os.environ["MODEL_API_KEY"]}],
    )
    anyo.add_hook(
        provider="github",
        action="pr_created",
        organization=os.environ["ANYO_GITHUB_ORGANIZATION"],
        repository=os.environ["ANYO_GITHUB_REPOSITORY"],
        pipeline=review_pull_request,
        hook_id="opencode-review-pull-request",
    )
    anyo.start(
        host="0.0.0.0",
        queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/queue.sqlite3"),
        queue_namespace=os.environ.get("ANYO_QUEUE_NAMESPACE", "opencode-review"),
        role=os.environ.get("ANYO_ROLE", "all"),
    )
