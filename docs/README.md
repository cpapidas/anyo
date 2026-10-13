# Anyo documentation

## For Humans

Start with the [wiki](wiki/README.md) for short how-to guides and examples. This index also links to detailed contracts, decisions, and completed work.

## Organization

- `wiki/`: short how-to guides with examples. Start at the [wiki index](wiki/README.md).
- `knowledge/`: requirements, research, decisions, and design. Shared topics live directly here; component-specific topics may use subdirectories once component boundaries are established.
- `tasks/`: each agreed task has a descriptive folder without a numeric prefix, with `plan.md` as its entry point: `tasks/<descriptive-task-name>/plan.md`. Supporting documents stay in the same folder.
- Create additional directories when there is actual content to store. Link related documents instead of duplicating them.

## Knowledge index

- [Notion hooks](knowledge/notion-hooks.md) — `accepted`; token collection, signed native events, exact source/entity filters, durable identity, and worker transport; live platform delivery remains unverified.
- [Better Stack hooks](knowledge/betterstack-hooks.md) — `accepted`; incident/error registration, authentication, native context, durable identity, and verified limits.
- [Repository workspaces](knowledge/repository-workspaces.md) — `accepted`; exact commits, persistent caches, sync/async ownership, credentials, cleanup, and bundle seeds.

- [Operations UI](knowledge/operations-ui.md) — `accepted`; protected dashboard setup, run and worker controls, process isolation, unified logs, retention, and deployment limits.
- [Worker queues](knowledge/worker-queues.md) — `draft`; SQLite/Redis setup, storage selection, durable identity, leases, replay policy, worker lifecycle, and backend cutover; verified implementation and deployment limits.
- [Product direction](knowledge/product-overview.md) — `draft`; confirmed requirements, proposed workflow behavior, and open implementation choices.
- [Webhook triggering contract](knowledge/webhook-triggers.md) — `draft`; hook registration, provider and response contracts, execution, delivery limits, and tooling decisions.
- [Slack hooks](knowledge/slack-hooks.md) — `draft`; app setup, native selectors, immediate responses, retry identity, and conditional platform coverage.

- [OpenCode prompt execution](knowledge/opencode-prompts.md) — `accepted`; code configuration, sync/async calls, typed results, progress, lifecycle, and Docker deployment.
- [Native OmO prompt execution](knowledge/omo-prompts.md) — `accepted`; selectable native harness, stdio RPC, model restrictions, task delegation, and owned process lifecycle.

## Task index

- [Open logged tasks and inspect their logs](tasks/task-log-navigation/plan.md) — `done`; searchable logged tasks, retained output after queue expiry, live filters and bookmarked task views; 881 installed-wheel tests and 17 real Redis checks passed.
- [Publish Anyo as a Python library](tasks/publish-library/plan.md) — `done`; MIT-licensed `0.1.0a2` published and verified from PyPI after the macOS cleanup fix; automatic alpha releases remain active on `main`.
- [Embed Anyo in a host application and extend its HTTP app](tasks/embeddable-http-application/plan.md) — `done`; public app access, external hosting, routes and middleware, composed lifecycle, and mounted dashboard support; 878 installed-wheel tests, real HTTP/Redis checks, and browser verification passed. [Hosting guide](wiki/http-hosting.md).
- [Start pipelines from Notion events](tasks/notion-hooks/plan.md) — `done`; signed webhooks, setup-only token collection, native event filters, and durable transport; 858 installed-wheel tests plus two real Redis worker checks passed. [Research and platform limits](tasks/notion-hooks/research.md).
- [Start pipelines from Better Stack incidents and errors](tasks/betterstack-hooks/plan.md) — `done`; authenticated incident/error hooks, typed events, SQLite/Redis workers, and setup example; 721 tests passed against the installed wheel plus two real Redis worker checks. [Research and platform limits](tasks/betterstack-hooks/research.md).
- [Clone repositories and reuse cached Git data across workers](tasks/repository-workspaces/plan.md) — `done`; exact-commit workspaces, persistent caches, cleanup, bundle seeds, and runnable project/worker examples; verified installed wheel, SQLite/Redis workers, and container replacement.
- [Build a protected operations UI](tasks/protected-operations-ui/plan.md) — `done`; environment-configured login, Runs and retry, process-based run/worker controls, and unified logs; verified browser flows, real Redis, and 581 tests against the clean installed wheel.
- [Simplify the harness example and bundle both executables](tasks/minimal-harness-example/plan.md) — `done`; one Python file and one Markdown prompt, Docker-installed OmO/OpenCode, and passing MiniMax reviews on ARM64 and AMD64.
- [Add native OmO as a selectable harness](tasks/native-omo-harness/plan.md) — `done`; direct native OmO integration, a selectable MiniMax review, 537 local tests, and live verification of both harnesses from an installed wheel.
- [Install OmO and demonstrate a MiniMax review](tasks/omo-minimax-example/plan.md) — `done`, superseded; historical plugin experiment, replaced by the native harness task.
- [Execute webhook pipelines with SQLite and Redis workers](tasks/sqlite-redis-workers/plan.md) — `done`; interchangeable queue backends, SQLite by default without an external service, independent workers, durable delivery state, and configuration-based switching.
- [Execute prompt files through OpenCode](tasks/opencode-prompt-execution/plan.md) — `done`; implemented code-based harness configuration, Python invocation, structured results, live progress, owned local processes, and a same-container [Dockerfile](../Dockerfile), with ARM64/AMD64 container and clean installed-package verification. Worker queues are tracked in their own task; [research](tasks/opencode-prompt-execution/research.md) records upstream capabilities and version discrepancies.
- [Support Slack hooks across events and interactions](tasks/slack-hooks/plan.md) — `done`; delivered Events API, commands, and interactions, with [coverage inventory](tasks/slack-hooks/coverage.md), [platform research](tasks/slack-hooks/research.md), and installed-package verification.
- [Organize hook providers into packages](tasks/provider-packages/plan.md) — `done`; gave GitHub its own package, documented the provider pattern, and verified unchanged behavior and installed-package contents.
- [Design and implement webhook triggers](tasks/webhook-triggering-system/plan.md) — `done`; delivered GitHub PR-created webhooks, bounded pipeline execution, setup examples, and installed-package verification.

## Writing workflow

Use [anyo-readme-wiki](../skills/anyo-readme-wiki/SKILL.md) to keep the README short, write simple examples, and put how-to guides in the wiki.

Use [anyo-docs](../skills/anyo-docs/SKILL.md) for shared writing rules, knowledge, task structure, and verification. Use [anyo-structure](../skills/anyo-structure/SKILL.md) for Python package layout and dependency boundaries.

Completed library guidance is available in the [Python](../skills/anyo-python/SKILL.md), [testing](../skills/anyo-testing/SKILL.md), and [packaging](../skills/anyo-packaging/SKILL.md) skills.
