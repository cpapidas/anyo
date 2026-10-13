# Anyo 0.1.0a1 release notes

## For Humans

This first alpha packages Anyo's event-driven AI pipeline capabilities for Python applications. These notes describe the prepared candidate; they are not a publication announcement.

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

Webhook reception is covered by deterministic fixtures. Live Better Stack and Notion account delivery remains unverified; see their contracts before relying on account-specific event availability. Dedicated script execution, broader orchestration, and prompt optimization remain future work.

## Verification

The [release task](plan.md) records exact checks, environments, access blockers, and eventual publication results.
