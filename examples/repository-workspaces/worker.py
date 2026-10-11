"""Receive GitHub PRs and prepare their exact commits inside durable workers."""

import os

import anyo
from anyo import HookEvent
from anyo.repositories import HTTPSCredentials, RepositoryCache


def prepare_pull_request(event: HookEvent) -> None:
    if event.head_sha is None or event.pull_request_number is None:
        raise ValueError("A PR commit and number are required")

    # Create inert configuration inside the callback so spawned workers need
    # neither open parent clients nor inherited in-memory cache state.
    cache = RepositoryCache(
        os.environ.get("ANYO_REPOSITORY_CACHE", ".anyo/repositories"),
        namespace=os.environ.get("ANYO_REPOSITORY_NAMESPACE", "review-app"),
    )
    token = os.environ.get("REPOSITORY_TOKEN")
    credentials = (
        HTTPSCredentials(os.environ.get("REPOSITORY_USERNAME", "x-access-token"), token)
        if token
        else None
    )
    with cache.checkout(
        # Use the registered project's trusted URL, never a payload clone URL.
        url=os.environ.get("REPOSITORY_URL"),
        local_path=os.environ.get("REPOSITORY_LOCAL_PATH"),
        commit=event.head_sha,
        fetch_ref=f"refs/pull/{event.pull_request_number}/head",
        credentials=credentials,
    ) as checkout:
        print(
            f"PR #{event.pull_request_number}: {checkout.commit_sha}; "
            f"cache hit: {checkout.cache_hit}; workspace: {checkout.path}",
            flush=True,
        )
        for warning in checkout.warnings:
            print(f"Content limit: {warning}", flush=True)
        # Replace this summary with your pipeline, keeping all file users inside
        # the context. After configuring a harness, you can call:
        # result = anyo.inject_prompt("review.md", workspace=checkout.path)
        # result.raise_for_failure()
        names = sorted(p.name for p in checkout.path.iterdir() if p.name != ".git")
        print(f"Repository root entries: {', '.join(names[:20])}", flush=True)


def main() -> None:
    anyo.add_hook(
        provider="github",
        action="pr_created",
        organization=os.environ["ANYO_GITHUB_ORGANIZATION"],
        repository=os.environ["ANYO_GITHUB_REPOSITORY"],
        pipeline=prepare_pull_request,
        hook_id="prepare-pull-request-repository",
    )
    anyo.start(
        queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/repositories.sqlite3"),
        queue_namespace=os.environ.get("ANYO_QUEUE_NAMESPACE", "repository-example"),
        role=os.environ.get("ANYO_ROLE", "all"),
        execution_mode=os.environ.get("ANYO_EXECUTION_MODE", "process"),
        host=os.environ.get("ANYO_HOST", "127.0.0.1"),
        port=int(os.environ.get("ANYO_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
