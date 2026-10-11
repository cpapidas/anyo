# Prepare repository files

## For Humans

Give each job its own copy of a repository at the exact commit it needs. Anyo keeps downloaded Git data so the next worker can reuse it.

## Check out a commit

Install Git 2.39 or later on Linux or macOS. Then:

```python
from anyo.repositories import RepositoryCache

cache = RepositoryCache(".anyo/repositories", namespace="review-app")

with cache.checkout(
    url="https://github.com/your-org/your-repo.git",
    commit="replace-with-the-full-commit-sha",
) as checkout:
    print(checkout.path)
    print(checkout.commit_sha)
    # Read or edit repository files here.
```

The `with` block owns the workspace. When it ends, Anyo removes that workspace and keeps the cache.

A cached commit works without contacting the remote. A new commit fetches missing Git data.

## Use it in a PR review job

After [configuring your AI tool](run-prompts.md), your hook can do this:

```python
import anyo
from anyo.repositories import RepositoryCache


def review_pull_request(event):
    cache = RepositoryCache(".anyo/repositories", namespace="review-app")
    with cache.checkout(
        url="https://github.com/your-org/your-repo.git",
        commit=event.head_sha,
        fetch_ref=f"refs/pull/{event.pull_request_number}/head",
    ) as checkout:
        result = anyo.inject_prompt("review.md", workspace=checkout.path)
        result.raise_for_failure()
        print(result.success.data)
```

Use your configured repository URL. The PR ref also works for fork PRs; Anyo still checks the event's exact SHA. Prepare any base commit or diff your review prompt needs.

For async jobs, use `async with cache.acheckout(...)` and `await anyo.ainject_prompt(...)`.

## Private repositories

Pass credentials separately from the URL:

```python
import os
from anyo.repositories import HTTPSCredentials

credentials = HTTPSCredentials("x-access-token", os.environ["REPOSITORY_TOKEN"])
# Add credentials=credentials to cache.checkout(...).
```

Use a token with repository read access. This is separate from your webhook signing secret.

## Share the cache

- Same host: give workers the same cache directory and namespace.
- Containers: mount a persistent volume at the same absolute cache path in each container.
- Different hosts: use a local cache on each host; optionally [copy a bundle seed](repository-deployment.md).

Each job gets a separate workspace. Submodules stay uninitialized; Git LFS files stay as pointers. Check `checkout.warnings` for these limits.

Next: [run the project-pulling examples](../../examples/repository-workspaces/README.md), [deploy and seed caches](repository-deployment.md), or read the [full repository contract](../knowledge/repository-workspaces.md).

[All guides](README.md)
