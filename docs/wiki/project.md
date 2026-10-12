# Project status

## For Humans

Anyo is a Python library for AI pipelines written in code. This page separates what works today from what is still planned.

## Available now

- GitHub, Slack, Better Stack, and [Notion](notion-hooks.md) webhooks that call your Python functions.
- OpenCode and native OmO prompt execution with structured results and progress.
- Repository checkouts at exact commits, with cached Git data shared across workers.
- SQLite or Redis queues, with optional separate workers.
- A protected dashboard for runs, logs, retries, and worker controls.
- [External HTTP hosting and app extensions](http-hosting.md), including mounted webhooks and dashboards.

Anyo uses Python 3.12 or later, one import package under `src/anyo/`, Starlette, and Uvicorn. Each hook provider has its own package under `src/anyo/integrations/`.

## Still planned

- A dedicated runner for user scripts.
- Broader agent orchestration.
- Prompt improvement using outcomes, evaluation, and feedback.

You can already call your own code inside a pipeline. You configure remote webhooks and public HTTPS hosting yourself.

## Release status

This is a local development distribution. The version is defined in [pyproject.toml](../../pyproject.toml); licensing and package publication remain undecided.

Read the [product direction](../knowledge/product-overview.md) for decisions and open questions, or the [provider guide](../knowledge/webhook-triggers.md#provider-packages-and-extension-contract) to add an integration.

[All guides](README.md)
