# Start Anyo pipelines from Better Stack incidents and errors

## For Humans

Better Stack incidents and application errors can now start Anyo pipelines through the existing hook and worker system. This task records the provider, its setup example, and the verification results. Start with [Connect Better Stack](../../wiki/betterstack-hooks.md); the [contract](../../knowledge/betterstack-hooks.md) explains filtering and delivery limits.

Status: done  
Created: 2026-10-11  
Last updated: 2026-10-11  
Scope: delivered incident/error webhook reception, typed events, durable worker transport, tests, example, and documentation. Live Better Stack account delivery remains unverified.

## Objective and authorization

Support **Better Stack incident or error → authenticated Anyo hook → queued pipeline → worker execution** through `anyo.add_hook(...)` and `anyo.start()`.

The user requested independent research and task creation on 2026-10-11, then authorized implementation and testing, working alone without clarification. A process is an existing Anyo pipeline job. No new workflow engine, remote webhook provisioning, or automatic remediation was required.

Dependencies: the completed [provider organization](../provider-packages/plan.md) and [SQLite/Redis workers](../sqlite-redis-workers/plan.md). Related knowledge: [product direction](../../knowledge/product-overview.md), [webhook contract](../../knowledge/webhook-triggers.md), [worker queues](../../knowledge/worker-queues.md), and [operations UI](../../knowledge/operations-ui.md). Repository structure, Python, testing, packaging, documentation, and README/wiki skills were applied.

## Delivered behavior

| Component | Outcome |
| --- | --- |
| [Better Stack package](../../../src/anyo/integrations/betterstack/__init__.py) | Public `BetterStackProvider` and `BetterStackEvent`; local validation, authentication, mapping, and matching. No new dependencies. |
| [Provider](../../../src/anyo/integrations/betterstack/_provider.py) | `incident` and `error` actions, mandatory native event selector or explicit `*`, exact resource/context filters, and a five-second total receive/admission deadline. |
| [Decoder](../../../src/anyo/integrations/betterstack/_webhooks.py) | Constant-time secret-header comparison before strict JSON parsing; native payload preservation, optional context, safe failures, and a versioned canonical payload fingerprint. |
| [Event codec](../../../src/anyo/integrations/betterstack/_codec.py) | Finite JSON transport with a trusted class and verification of stored derived context; works in both receiver and worker. |
| [Default composition](../../../src/anyo/__init__.py) | Better Stack available through `anyo.add_hook` alongside GitHub and Slack; inert import/registration. |
| [Shared receiver](../../../src/anyo/application.py) and [contracts](../../../src/anyo/hooks.py) | Repeated headers retain ambiguity as comma-joined values instead of silently selecting one credential; generic authentication wording. No Better Stack branches in HTTP handling or execution. |
| [Tests](../../../tests/integrations/betterstack/test_provider.py) | Provider, HTTP admission, queue failures, retries, privacy, real local TCP reception, and separate SQLite/Redis workers. |
| [Runnable example](../../../examples/betterstack_webhook.py) | Incident and new-error callbacks, application filtering, configurable storage/roles, and thread/process execution. |
| [Setup guide](../../wiki/betterstack-hooks.md) and [contract](../../knowledge/betterstack-hooks.md) | Outgoing webhook configuration, local check, typed context, worker behavior, limits, and links from README/indexes. |

The endpoint is `POST /webhooks/betterstack`. Set `ANYO_BETTERSTACK_WEBHOOK_SECRET` or per-hook `secret=` and configure `X-Anyo-Webhook-Secret` in Better Stack. Successful admission, duplicates, and authenticated ignored events return an empty `200`. Queue failure/capacity rejection returns `503`. A callback runs independently of the HTTP response.

## Implementation decisions

- **Native event selection:** preserve the supplied discriminator without an allowlist. The documented default incident template omits it, so missing incident names become `unspecified`; timestamps never imply a lifecycle transition. The example uses `*` and instructs consumers to select creation/reopening upstream. Exact filters require a confirmed native name. Missing error event names remain invalid.
- **Scope:** receive direct errors without requiring incidents. Log/metric alert routing is documented as alert → Better Stack incident → this provider. Monitor, on-call, release, and status-page pipelines remain excluded. Ordinary test notifications can run the pipeline.
- **Authentication:** use the documented custom-header capability. No signature scheme, API token, Basic-auth fallback, or replay-freshness guarantee is invented. Different hook secrets authorize only their own hooks.
- **Context:** error team/application/environment filters and incident monitor filters use evidenced fields. Missing values do not satisfy an exact filter. Incident team context is not inferred. Full payloads remain available privately; no default enrichment or complete-stack-trace promise.
- **Identity:** no verified upstream delivery ID exists for both families. Hash the complete parsed payload with sorted keys and compact UTF-8 JSON, versioned as `betterstack:payload:v1:`. Formatting changes do not create new work. Distinguishable transitions retain distinct IDs. Changed retry bodies can duplicate work, and identical distinct transitions can collapse during retention. These limitations are explicit rather than hidden behind a resource-only key.
- **Execution:** reuse memory/SQLite/Redis execution, stable hook identities, namespace checks, optional retries, private payload copies, and worker controls. Direct errors and incidents derived from the same error remain independent triggers. Existing ordering and exactly-once limitations continue to apply.

The original plan proposed native selectors, a provider-owned codec, a shared-secret header, and a five-second deadline. Implementation retained these choices and resolved missing incident discriminators with the explicit sentinel above. The [research coverage](research.md#implementation-coverage) distinguishes official examples from synthetic lifecycle names and unverified platform behavior.

## Completion criteria and evidence

- [x] Incident and direct error notifications reach callbacks through real local TCP HTTP reception. [Listener test](../../../tests/integrations/betterstack/test_server.py), [HTTP tests](../../../tests/integrations/betterstack/test_application.py), and the installed example smoke cover both families.
- [x] Authentication fails before JSON interpretation; distinct hook secrets stay isolated; repeated credentials, malformed/duplicate JSON, invalid encodings/numbers/context, nesting, body limits, and unsupported families have safe outcomes.
- [x] Explicit event selection, exact filters, absent monitor/context, unknown event names, and private callback payload copies behave as documented. Manual/Telemetry-shaped incidents work without assuming a monitor.
- [x] Identity tests cover canonical ordering, recurring errors, state changes, different comments, and the deliberate changed-retry/identical-transition limitations. Existing retained-history and failed/interrupted replay semantics are preserved.
- [x] `200` follows admission. Queue commit uncertainty, capacity rejection, atomic fan-out, deadlines, slow callbacks, and replay of failed hooks are verified without replaying successful siblings.
- [x] Typed fields survive separate receiver/worker interpreters, restart, and spawned process execution for SQLite and real Redis. Workers run without webhook secrets. The full regression suite preserves existing GitHub/Slack, queue retention, cancellation, and operations behavior.
- [x] Pytest, Ruff, mypy, installed consumer typing, source/wheel metadata and contents, base-only imports, and installed-package tests pass.
- [x] The runnable example, setup guide, research, contracts, product overview, and indexes describe delivered behavior. Local links and Python/shell examples are checked.

## Verification results

Run on 2026-10-11 with Python 3.12.14 on macOS arm64:

- Default checkout suite: **721 passed, 39 live tests deselected**. This includes 85 new default Better Stack tests.
- Clean installed-wheel suite outside the checkout, extracted from the source archive, using `python -I -m pytest -q`: **721 passed, 39 live tests deselected**. Imports resolve to `site-packages`; no editable install or source path override is used.
- Explicitly selected Better Stack Redis worker tests: **2 passed** against an isolated Redis 7.4 Docker container, in both the development environment and clean installed environment. They exercise thread/process callbacks, independent worker interpreters, persisted typed payloads, replacement workers, and deduplication. Temporary Redis resources were removed after verification.
- Ruff checks and formatting passed for `src`, `tests`, and `examples`; strict mypy passed for all 50 source modules and the new example. Installed-consumer typing passed for `Provider`, `ResponseProvider`, codec discovery, root registration, and narrowed event context.
- Built a fresh source distribution and wheel from that archive with isolated build dependencies. Twine checks passed. Wheel runtime files match the tested source byte-for-byte; all five Better Stack modules, typing marker, and existing runtime resources are included. The source archive includes the new tests and subprocess consumer fixture.
- Installed with base dependencies first, at the declared lower bounds **Starlette 1.7.0 / Uvicorn 0.53.0**. Before test/Redis dependencies were added, a real local HTTP smoke verified default provider registration, SQLite admission, and both typed callbacks without HTTPX2 or Redis installed. `pip check` passed after test tooling was added separately.
- The actual [example](../../../examples/betterstack_webhook.py) ran against the installed wheel from an isolated directory, accepted both families, printed summaries, and completed graceful SIGTERM shutdown. Uvicorn re-raises SIGTERM after cleanup, as in the existing server contract.
- Checked changed Markdown opening structure, local links/anchors, and Python snippets, plus shell syntax in the new setup guide.

Artifacts: [wheel](../../../dist/betterstack-hooks-verified/anyo-0.1.0.dev0-py3-none-any.whl) and [source archive](../../../dist/betterstack-hooks-verified/anyo-0.1.0.dev0.tar.gz). Distribution version and dependencies are unchanged; no package was published.

## Remaining platform limits

No live Better Stack account was connected or modified. Local fixtures establish Anyo behavior, not actual upstream delivery timing or exact undocumented wire spellings. Incident retry/timeouts, notification/escalation setup, retry-body stability, and account-specific payloads remain unverified. Error webhook facts and unresolved incident details are recorded in the [research](research.md).

The provider is usable with the documented envelopes; consumers must configure and test their own outgoing webhook. Payload-based identity has the limits above. The task does not automatically fetch telemetry, acknowledge/resolve incidents, send messages, open pull requests, or apply fixes. No paid model calls or production deployment were performed.

## Sources

- User conversations, 2026-10-11 (no share link supplied): initial research/task request, followed by explicit implementation/test authorization and instructions to work alone.
- [Official-source research](research.md), rechecked during implementation, and linked local contracts/code/tests.
- Recorded verification commands and fresh distribution artifacts above establish the implementation results.
