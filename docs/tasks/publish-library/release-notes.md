# Anyo 0.1.0a2 release notes

## For Humans

Anyo [0.1.0a2](https://pypi.org/project/anyo/0.1.0a2/) is the first published alpha of its event-driven AI pipeline library, under the MIT license. The [automatic release workflow](../../wiki/releases.md#how-versions-are-assigned) assigns a new alpha version to each release run.

## Included capabilities

- GitHub, Slack, Better Stack, and Notion webhook providers, with synchronous and asynchronous Python pipelines.
- SQLite queues by default, optional Redis storage, and separate receiver/worker processes.
- OpenCode and native OmO prompt execution with structured results and progress events.
- Exact-commit repository workspaces and persistent Git caches.
- A protected operations dashboard for runs, logs, retries, and worker controls.
- Public ASGI hosting, route and middleware extensions, and lifecycle composition for mounted applications.

## Installation and compatibility

Python 3.12 or later is required. Base installation includes the HTTP server and SQLite support; optional requirements are `anyo[redis]` and `anyo[opencode]`. Install with `python -m pip install 'anyo==0.1.0a2'`. See [installation](../../wiki/installation.md) for executable prerequisites and optional dependencies.

This is an alpha. Public APIs and durable storage formats may change; pin versions and upgrade processes sharing a queue together. Repository workspaces, native managed OmO, and process execution require Linux or macOS/POSIX support. External model executables are not included in the Python wheel.

Local Docker source builds use development version `0.0.dev0` by default. Builds from a release tag can supply its version through the `ANYO_VERSION` build argument; see the [Docker prompt example](../../wiki/run-prompts.md#try-the-docker-example).

Webhook reception is covered by deterministic fixtures. Live Better Stack and Notion account delivery remains unverified; see their contracts before relying on account-specific event availability. Dedicated script execution, broader orchestration, and prompt optimization remain future work.

## Verification

The [release task](plan.md) records the initial `0.1.0a1` check failure, the macOS cleanup fix in PR #5, and the successful `0.1.0a2` publication and installation checks.
