# Pull projects into cached workspaces

## For Humans

Use these examples to fetch project files before running your own pipeline. Each run gets a separate workspace, while downloaded Git history stays on disk for later workers. Start with one project, then use multiple projects together or prepare a pull request inside a background worker.

## Setup

Use Python 3.12+ and Git 2.39+ on Linux or macOS. From the Anyo repository root, with your Python environment active:

```sh
python -m pip install .
export ANYO_REPOSITORY_CACHE="$PWD/.anyo/repositories"
export ANYO_REPOSITORY_NAMESPACE='review-app'
```

Keep the cache directory and namespace the same across workers that should reuse data. The examples list files and make no model calls. They do not modify your original repository or push changes.

## Pull one project

Replace the URL and branch with your own values:

```sh
export REPOSITORY_URL='https://github.com/your-org/your-repo.git'
export REPOSITORY_REF='refs/heads/main'
python examples/repository_workspace.py
```

Run the command again to refresh that branch and fetch missing changes into the existing cache. `main` is only the example default; set `refs/heads/master` or another full ref if needed. This prepares a fresh workspace each time, without merging into an existing developer checkout.

To use an exact commit, clear the ref and supply a full commit SHA:

```sh
unset REPOSITORY_REF
export REPOSITORY_COMMIT='replace-with-the-full-commit-sha'
python examples/repository_workspace.py
python examples/repository_workspace.py
```

The second process prints `Pinned commit cache hit: True` and needs no origin contact when the commit is cached. A named ref always refreshes; it can still reuse history even though `cache_hit` describes only the pinned-commit fast path.

The workspace exists inside the script's `with cache.checkout(...)` block. Add file processing or a prompt call there. After the block finishes, Anyo removes those working files and keeps reusable Git data; the printed workspace path is not a permanent clone.

## Private projects

Provide `REPOSITORY_TOKEN` through your shell or deployment's secret mechanism. For GitHub, the example uses `x-access-token` as the HTTPS username; set `REPOSITORY_USERNAME` for a server that expects a different username. Keep credentials out of `REPOSITORY_URL`.

The token needs repository read access. It is independent of the webhook signing secret. Public repositories require no token. For SSH, use the explicit `SSHCredentials` API described in the [repository contract](../../docs/knowledge/repository-workspaces.md#credentials-and-trust); these small runnable examples use HTTPS.

## Pull several projects together

[pull_projects.py](pull_projects.py) keeps all workspaces alive while your cross-project code runs:

```sh
python examples/repository-workspaces/pull_projects.py \
  'https://github.com/your-org/backend.git' \
  'https://github.com/your-org/frontend.git' \
  --ref refs/heads/main
```

It refreshes the supplied ref in each repository and prints their resolved commits. Each repository has its own cache entry. The sample applies the same ref and optional HTTPS credentials to every project; change the individual `cache.checkout(...)` calls if projects need different refs or credentials. `ExitStack` also releases earlier workspaces if preparing a later project fails.

For a local, network-free demonstration, pass existing Git checkouts explicitly:

```sh
python examples/repository-workspaces/pull_projects.py --local \
  /absolute/path/to/backend /absolute/path/to/frontend --ref refs/heads/main
```

The single-project example also accepts `REPOSITORY_LOCAL_PATH` in place of `REPOSITORY_URL`. Unset `REPOSITORY_TOKEN` for that local example and choose either `REPOSITORY_REF` or `REPOSITORY_COMMIT`.

## Pull PR code inside a worker

[worker.py](worker.py) receives a GitHub PR event and prepares the event's exact commit, including fork PRs through the base repository's PR ref. It prints the checkout's file names; put your pipeline code inside its checkout context.

```sh
export ANYO_GITHUB_ORGANIZATION='your-org'
export ANYO_GITHUB_REPOSITORY='your-repo'
export REPOSITORY_URL='https://github.com/your-org/your-repo.git'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
python examples/repository-workspaces/worker.py
```

Use a URL for the same base repository registered by the hook. [Connect its GitHub webhook](../README.md#connect-github), then open a pull request. This example uses SQLite and process execution, and supports `ANYO_EXECUTION_MODE=thread` too. A moved PR ref never replaces the SHA from the admitted event. For local fixtures, this worker also accepts `REPOSITORY_LOCAL_PATH` instead of the URL; the local repository must contain the event's commit or PR ref.

To run a receiver and independent workers on one host, give them the same absolute SQLite path:

```sh
export ANYO_QUEUE_URL="sqlite:///$PWD/.anyo/repositories.sqlite3"
ANYO_ROLE=receiver python examples/repository-workspaces/worker.py
```

In another terminal with the same queue, hook configuration, cache path, and namespace:

```sh
ANYO_ROLE=worker python examples/repository-workspaces/worker.py
```

Start another worker with that command to reuse the disk cache. Worker-only processes need the repository credentials for private projects and do not require the webhook signing secret. Receiver-only processes need no clone credentials. These environment variables are read by the examples, not automatically discovered by Anyo.

Redis is selected with `ANYO_QUEUE_URL` after installing `.[redis]`. Across machines, each host needs its own local cache or an imported [bundle seed](../../docs/wiki/repository-deployment.md#seed-a-new-host); Redis carries jobs, not Git objects. For containers, mount a persistent volume at the same absolute cache path as shown in the [deployment guide](../../docs/wiki/repository-deployment.md).

## Async pipelines and maintenance

Async code uses the same ownership boundary:

```python
async with cache.acheckout(url=repository_url, commit=commit_sha) as checkout:
    result = await anyo.ainject_prompt("review.md", workspace=checkout.path)
    result.raise_for_failure()
```

Configure the harness before using that prompt call. For periodic cleanup from your own application, call `cache.maintain()` outside active async work, or use `await asyncio.to_thread(cache.maintain)`. It recovers abandoned workspaces and evicts eligible idle data while preserving active users.

Submodules stay uninitialized and Git LFS files remain pointers; check the printed content warnings. See the [full contract](../../docs/knowledge/repository-workspaces.md) for credentials, limits, cleanup, and seed export/restore.
