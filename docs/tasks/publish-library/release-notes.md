# Anyo 0.1.0 alpha release notes

## For Humans

The first published alpha will package Anyo's event-driven AI pipeline capabilities for Python applications under the MIT license. These notes describe the initial release scope; publication is still pending. The exact `0.1.0aN` version is assigned by the [automatic release workflow](../../wiki/releases.md#how-versions-are-assigned).

## Included capabilities

- GitHub, Slack, Better Stack, and Notion webhook providers, with synchronous and asynchronous Python pipelines.
- SQLite queues by default, optional Redis storage, and separate receiver/worker processes.
- OpenCode and native OmO prompt execution with structured results and progress events.
- Exact-commit repository workspaces and persistent Git caches.
- A protected operations dashboard for runs, logs, retries, and worker controls.
- Public ASGI hosting, route and middleware extensions, and lifecycle composition for mounted applications.

## Installation and compatibility

Python 3.12 or later is required. Base installation includes the HTTP server and SQLite support; optional requirements are `anyo[redis]` and `anyo[opencode]`. See [installation](../../wiki/installation.md) for executable prerequisites and the first publication's status.

This is an alpha. Public APIs and durable storage formats may change; pin versions and upgrade processes sharing a queue together. Repository workspaces, native managed OmO, and process execution require Linux or macOS/POSIX support. External model executables are not included in the Python wheel.

Local Docker source builds use development version `0.0.dev0` by default. Builds from a release tag can supply its version through the `ANYO_VERSION` build argument; see the [Docker prompt example](../../wiki/run-prompts.md#try-the-docker-example).

Webhook reception is covered by deterministic fixtures. Live Better Stack and Notion account delivery remains unverified; see their contracts before relying on account-specific event availability. Dedicated script execution, broader orchestration, and prompt optimization remain future work.

## Verification

The [release task](plan.md) records exact checks, environments, access blockers, and eventual publication results.
