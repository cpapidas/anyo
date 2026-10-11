# Anyo product direction

## For Humans

A developer wants to respond to a GitHub event, run their own code, ask agents to analyze what happened, and use the results in later steps. Anyo is intended to make workflows like this programmable while allowing prompts to improve through feedback on their outcomes. This document captures the agreed direction and separates it from implementation ideas that still need design.

Status: draft  
Last updated: 2026-10-11
Scope: Anyo product direction and initial workspace decisions.

## Confirmed direction

- The project is named **Anyo**.
- Build a custom AI pipeline framework in **Python**. Python replaces the earlier discussion of a Go runtime for this framework.
- Deliver Anyo as an installable library with one import package under `src/anyo/`. Organize code by capability, start with shallow modules, and grow them into subpackages as implementation requires. The [package structure skill](../../skills/anyo-structure/SKILL.md) records ownership, dependency boundaries, and public API conventions.
- Let developers configure agents and write workflow behavior in code, including conditional steps and iteration over results.
- Start event-driven pipelines through registered hooks. The consumer installs Anyo in a Python project, calls `anyo.add_hook(...)` for each event-to-pipeline binding, and calls `anyo.start()` in the main file to start receiving events with useful runtime defaults.
- Design for multiple trigger providers, with GitHub webhooks first. The initial registration uses `provider="github"`, `action="pr_created"`, `organization`, `repository`, and a `pipeline` callable. Multiple hooks can be registered before startup. The [webhook triggering task](../tasks/webhook-triggering-system/plan.md) delivered this first implementation; its [contract](webhook-triggers.md) records the engineering decisions.
- Give each hook provider a dedicated package under `src/anyo/integrations/<provider>/`, starting with `github/`. Each package owns provider-specific configuration and event handling behind the shared `Provider` interface. The [provider organization task](../tasks/provider-packages/plan.md) preserves the public API while establishing this pattern.
- Add comprehensive Slack hook support after GitHub, covering actions exposed through Slack app webhooks. The [Slack hooks task](../tasks/slack-hooks/plan.md) implements native events and subtypes, commands, and interactions with immediate response data. The [setup guide](slack-hooks.md) records its API and the [inventory](../tasks/slack-hooks/coverage.md) distinguishes implemented reception from conditional subscriptions and unavailable surfaces.
- Add Better Stack hooks so an incident or application error can start an Anyo pipeline. The [Better Stack task](../tasks/betterstack-hooks/plan.md) implements direct incident/error webhooks with typed events and existing durable workers. The [contract](betterstack-hooks.md) records authentication and payload-identity limits; [research](../tasks/betterstack-hooks/research.md) distinguishes local verification from live platform gaps and describes log alerts routed through incidents.
- Include user-provided scripts as a desired framework capability.
- Add Python invocation of OpenCode using user prompt files, a library-owned JSON success/failure envelope, and optional live progress. Install OpenCode alongside Anyo in the consumer container and let Python start it locally, without Docker-in-Docker. The [OpenCode task](../tasks/opencode-prompt-execution/plan.md) records the delivered implementation and verification; the [API guide](opencode-prompts.md) describes the current contract. The later worker/queue system is explicitly outside its scope.
- Configure OpenCode in Python through `anyo.harness(provider="opencode", plugins=[...], models=[{"model": "...", "key": "..."}])`. Anyo translates the registered configuration for its owned runtime; consumers need not manually write OpenCode configuration. Writing and verifying the Dockerfile is an explicit deliverable of the OpenCode task.
- Let users choose native OmO or OpenCode through the same harness and prompt APIs. `provider="omo"` owns the standalone OmO executable; its [contract](omo-prompts.md) and [implementation task](../tasks/native-omo-harness/plan.md) describe native delegation, process ownership, model selection, and verification. The [minimal MiniMax example](../../examples/README.md#choose-opencode-or-native-omo) contains one Python file and one Markdown prompt. The shared Docker image installs both harnesses and runs the example without a separate setup script.
- Add worker execution with both SQLite and Redis queue backends after OpenCode, allowing users to switch through configuration while retaining their pipeline code. SQLite provides the path without an external queue service. The completed [worker task](../tasks/sqlite-redis-workers/plan.md) records implementation and verification. Its [worker guide](worker-queues.md) describes the SQLite default, optional Redis adapter, stable identities, replay policy, and separate receiver/worker modes.
- Let users clone their repositories for pipeline work and reuse cached repository data when new workers start, avoiding a full repository download for each worker. The [repository workspace contract](repository-workspaces.md) describes `RepositoryCache`, exact-commit workspaces, persistent local caches, and portable seeds. The [implementation task](../tasks/repository-workspaces/plan.md) records verification.
- Support improving prompts based on outcomes and feedback.
- Provide an optional protected operations UI with environment-supplied credentials, run inspection and retry, run cancellation and worker controls, and unified live/history logs. The [operations guide](operations-ui.md) describes the delivered behavior and limits; the [implementation task](../tasks/protected-operations-ui/plan.md) records its design and verification. Process mode provides targeted force stop on POSIX while the existing thread execution mode remains the default.
- Keep Anyo in its own project workspace.
- Reuse Metis's documentation philosophy: English documents, an opening `For Humans` narrative, knowledge and tasks separated, and descriptive task folders whose entry point is `plan.md`.
- Keep the Python library structure independent of Metis's application-scaffolding rules. The structural map does not require implementing every capability immediately.
- Use focused repository-local guidance for [Python library development](../../skills/anyo-python/SKILL.md), [testing](../../skills/anyo-testing/SKILL.md), and [packaging](../../skills/anyo-packaging/SKILL.md). These skills preserve open tooling and runtime decisions; dedicated execution and evaluation skills are deferred until their designs are concrete.

## Motivating workflow

The original example listens for a GitHub pull-request event, checks out a specific commit, runs a prompt-based action, and conditionally processes related repositories identified in the result. Metis's change-documentation workflow is a motivating use case: it connects repository changes to related discussions and documents so readers can understand what changed, why, and how.

GitHub PR-created webhooks are implemented as the first trigger. Repository cloning and worker cache reuse are implemented through [RepositoryCache](repository-workspaces.md); the broader related-repository analysis workflow remains illustrative, and no Metis dependency has been selected. The earlier suggestion to start with GitHub Actions was superseded by the user's choice of webhooks.

## Implemented foundation

The first implementation uses Python 3.12 or later, setuptools, and standard `venv`/`pip` workflows. Starlette receives webhooks and Uvicorn serves the default listener. These are implementation decisions made while carrying out the requested task; broader workflow execution and release policy remain open.

Hooks receive a typed event containing the target repository, PR number, commit SHA, delivery ID, and payload. Both synchronous and asynchronous callbacks are supported. The default listener is `127.0.0.1:8000/webhooks/github`, with four concurrent pipelines and a capacity of 100 running or queued jobs. Provider adapters extend the trigger system without adding GitHub behavior to its core.

Slack uses `/webhooks/slack`, a `SlackEvent` with optional native identifiers, and case-sensitive app/workspace/enterprise filters. Ordinary pipelines still run in the background. Separate async responders return `HookResponse` for immediate command replies, dynamic options, and modal validation; their concurrency and replay history are bounded independently. Slack protocol rules stay in its integration package, with generic response contracts shared by the application and execution modules. No Slack SDK dependency is required.

The consumer configures the GitHub repository webhook and public HTTPS endpoint manually. Anyo validates each hook's secret, routes matching deliveries, and persists bounded per-hook delivery history in the default SQLite queue. Redis can replace SQLite through configuration; both support worker claims, cross-process deduplication within a namespace, explicit failed/interrupted redelivery, and optional bounded retries. Durable shutdown finishes owned attempts and preserves backlog. The compatibility memory runtime retains its original local history and full-drain behavior. See the [runnable setup guide](../../examples/README.md) for the complete consumer flow and the [webhook contract](webhook-triggers.md) for guarantees and limits.

The optional dashboard at `/ui/` uses an environment-configured operator account and shares the receiver's HTTP application. SQLite/Redis operations record worker state, cancellation requests, confirmed outcomes, and bounded logs. Targeted force stop requires process execution with an importable top-level callback; synchronous threads cannot be killed independently. Known credentials are redacted before diagnostic storage, and unavailable or ambiguous output is not presented as a complete run history. The [operations guide](operations-ui.md) records authentication, retention, namespace upgrade, and log coverage details.

## Proposed approaches

These are design suggestions from the discussion, not accepted implementation contracts:

- Treat scripts and agent calls as observable workflow steps with identifiable inputs, outputs, and versions.
- Execute custom scripts through a runner, with JSON input and output and a separate channel for logs. Supported languages, runtimes, dependencies, and isolation remain to be decided.
- Let custom evaluators return scores and explanatory feedback for prompt optimization.
- Generate candidate prompt versions, compare their outcomes against a baseline on separate evaluation examples, and activate a candidate according to a defined policy.
- Keep evaluator versions fixed during comparisons, and preserve the prompt version used by each run.
- Define timeouts, retries, and recovery with the effects of each step in mind.

## Open choices

- Workflow and agent authoring APIs beyond hook registration, event callbacks, and startup; broader workflow execution architecture.
- Broader agent orchestration beyond the implemented [OpenCode](opencode-prompts.md) and [native OmO](omo-prompts.md) invocation contracts.
- Script execution contract and how runtime environments are prepared.
- Outcome metrics, feedback collection, optimization strategy, and promotion policy.
- Broader worker orchestration beyond the [SQLite and Redis contract](worker-queues.md), automatic remote webhook provisioning, and future integrations beyond the implemented GitHub, Slack, and Better Stack providers.
- License, package-index name availability, versioning policy, and release destination. The current `anyo` distribution is local development version `0.1.0.dev0`; no publication is implied.

## Sources

- Project conversation, recorded on 2026-10-01 (no share link supplied): developer-authored AI pipelines, outcome-based prompt improvement, the name Anyo, user scripts, and the decision to create a Python workspace with Metis-style documentation. Suggested mechanisms above remain proposals unless explicitly accepted.
- Project conversation, recorded on 2026-10-03 (no share link supplied): clarification that Anyo is a package/library, approval of the proposed capability-based `src/anyo/` structure, and a request to create a focused repository-local skill for it.
- Project conversation, recorded on 2026-10-03 (no share link supplied): approval to create the [Python](../../skills/anyo-python/SKILL.md), [testing](../../skills/anyo-testing/SKILL.md), and [packaging](../../skills/anyo-packaging/SKILL.md) skills following the recommendation to add these next and defer execution and evaluation guidance.
- Project conversation, recorded on 2026-10-03 (no share link supplied): selected webhooks and GitHub as the first trigger provider, specified the `anyo.add_hook(...)` and `anyo.start()` consumer flow, then authorized implementation of [the triggering-system task](../tasks/webhook-triggering-system/plan.md). The implementation outcome and verification are recorded in that task.
- Project conversation, recorded on 2026-10-03 (no share link supplied): requested a dedicated folder for every hook provider and a consistent provider abstraction, starting with GitHub.
- Project conversation, recorded on 2026-10-03 (no share link supplied): requested independent research and creation of a task for Slack hooks covering all possible actions, following the GitHub integration, then authorized implementation. The [Slack task](../tasks/slack-hooks/plan.md) records the scope, delivered contracts, and verification.
- [Documentation skill](../../skills/anyo-docs/SKILL.md): the adapted writing and task-organization conventions.
- Project conversation, recorded on 2026-10-03 (no share link supplied): requested research and task creation for Python-to-OpenCode prompt execution, structured success/failure results, live logs, and a same-container deployment; explicitly excluded the subsequent worker/queue work and did not request implementation yet.
- Project follow-up, recorded on 2026-10-03 (no share link supplied): required code-based OpenCode configuration through `anyo.harness` with plugins and model/key entries, and reiterated the Dockerfile deliverable.

- Project follow-up, 2026-10-03 (no share link supplied): explicitly authorized implementation of the OpenCode task.
- Project follow-up, 2026-10-04 (no share link supplied): requested background workers after OpenCode with a simple setup path, then requested creation of the [SQLite and Redis worker task](../tasks/sqlite-redis-workers/plan.md) with easy backend switching, before implementation.

- Project follow-up, 2026-10-04 (no share link supplied): authorized implementation of the SQLite and Redis worker task after reviewing the plan.
- Project follow-up, 2026-10-06 (no share link supplied): requested removal of the OmO plugin example and a choice of OpenCode versus native OmO harnesses, with autonomous live validation using MiniMax only.
- Project follow-up, 2026-10-08 (no share link supplied): requested a very small example consisting of one Python and one Markdown file, with both executables installed by the Dockerfile and no setup script.
- Project conversation, 2026-10-11 (no share link supplied): requested a task for a UI protected with username/password from environment variables, cancellation of running or stuck work, and unified OpenCode/OmO and general logs, following discussion of run inspection, retry, and separate run/worker controls; subsequently authorized the implementation recorded in the [operations UI task](../tasks/protected-operations-ui/plan.md).
- Project conversation, 2026-10-11 (no share link supplied): requested a task plan for cloning users' repositories and caching Git data for reuse when new workers spawn; requested planning alone. The [repository workspace task](../tasks/repository-workspaces/plan.md) separates that requirement from proposed cache and workspace mechanisms.

- Project follow-up, 2026-10-11 (no share link supplied): authorized implementation of the repository workspace task.
- Project conversation, 2026-10-11 (no share link supplied): requested research and task creation for Better Stack incident/error hooks that start an Anyo process; requested autonomous work without delegation or clarification. The initial [task](../tasks/betterstack-hooks/plan.md) recorded planning before the implementation follow-up below.
- Project follow-up, 2026-10-11 (no share link supplied): authorized implementation and testing of the Better Stack task, working alone without clarification. Its [contract](betterstack-hooks.md) records the delivered behavior and verification limits.
