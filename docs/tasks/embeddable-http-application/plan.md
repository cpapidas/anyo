# Embed Anyo in a host application and extend its HTTP app

## For Humans

A developer should be able to use Anyo inside an existing Python service, choose the HTTP server, and add their own routes and middleware to Anyo's HTTP application. The existing one-call server remains useful for small projects. This task makes both approaches public, documented, and testable while preserving the same hooks, queues, and workers.

Status: done

Created: 2026-10-12

Last updated: 2026-10-12

## Objective and authorization

Make HTTP hosting a consumer choice: run Anyo with the existing convenience server, serve it through a consumer-owned host, or extend Anyo's HTTP application before serving it. Consumers must not need private attributes, monkeypatching, or a fork of Anyo.

The user requested creation of this task on 2026-10-12, working alone without clarification, then authorized implementation. The public APIs below are implemented and verified through contract tests, real HTTP hosts, an installed wheel, and a mounted dashboard in the browser.

Related knowledge: [product direction](../../knowledge/product-overview.md), [webhook contract](../../knowledge/webhook-triggers.md#startup-and-configuration), [worker queues](../../knowledge/worker-queues.md), and [operations UI](../../knowledge/operations-ui.md). Build on the completed [webhook](../webhook-triggering-system/plan.md), [worker](../sqlite-redis-workers/plan.md), and [dashboard](../protected-operations-ui/plan.md) tasks.

## Historical foundation and gaps

Anyo is already an installable Python library. The work is to complete its hosting and extension contract, rather than create a separate distribution.

| Current behavior | Consequence for this task |
| --- | --- |
| `Application.asgi()` returns a public Starlette app without starting a listener. Its lifespan owns dispatchers and workers. | Reuse this integration boundary; external ASGI hosting already has a foundation. |
| Package-level `add_hook`, `harness`, `start`, and `worker` use a private default application, with built-in providers and SQLite/Redis composition. There is no matching package-level ASGI factory. | Expose HTTP access for that same registry without requiring consumers to reach into `anyo._application`. |
| An explicit `Application` accepts provider, harness, and queue adapters, but defaults to an empty registry and memory execution. | Provide an easy isolated instance with standard defaults without silently changing the advanced constructor's behavior. |
| `start()` privately builds the HTTP app and then runs Uvicorn. | Define how consumers customize the actual app that the convenience server serves; a separately constructed app currently does not customize that instance. |
| HTTP reception requires an active lifespan and returns `503` without it. Registration freezes while running. | Make startup, mounted-app lifecycle composition, shutdown, and repeated-use rules explicit. |
| Dashboard routing, redirects, cookie paths, HTML assets, and browser requests use `/ui` paths. | Audit and fix prefix handling before promising mounted dashboard support. |
| Starlette and Uvicorn are base dependencies; `application.py` imports Uvicorn directly. | Separate server ownership from library use. Dependency optionality is a compatibility decision, not a prerequisite for embedding. |

These findings describe the source inspection at task creation. The implementation below addresses the hosting and extension gaps while preserving the existing distribution and dependency defaults.

## Scope and required behavior

- Preserve the existing `anyo.add_hook(...)` / `anyo.start(...)` workflow, default SQLite storage, webhook URLs, and worker-only mode.
- Offer public access to an HTTP application that uses the hooks, harness, queue configuration, and operations settings the consumer registered. Also support isolated application instances without shared registrations or lifecycle state.
- Allow a consumer to serve Anyo through their own ASGI server and mount it alongside their application's routes. Support a non-root prefix such as `/anyo`, with webhooks at `/anyo/webhooks/{provider}` and the optional dashboard at `/anyo/ui/`.
- Allow consumers to add routes, middleware, and application startup/shutdown work before serving Anyo. Define route precedence, reserved Anyo paths, and middleware effects on raw webhook bodies and authentication.
- Give the host ownership of its event loop, sockets, process signals, and logging. Anyo starts and closes only its owned workers, dispatchers, response tasks, queue resources, and harness resources. Cleanup must also cover partial startup failure and cancellation.
- Preserve provider authentication, exact-body signature verification, request limits, immediate Slack responses, durable admission and deduplication, receiver/worker roles, and operations authentication in every supported hosting mode.

Affected components are `anyo.application`, the package-root composition API, `anyo.ui` and its assets, focused lifecycle/HTTP tests, consumer examples, and documentation. Queue and provider implementations should change only where the hosting contract requires it. Follow the [structure](../../../skills/anyo-structure/SKILL.md), [Python](../../../skills/anyo-python/SKILL.md), [testing](../../../skills/anyo-testing/SKILL.md), [packaging](../../../skills/anyo-packaging/SKILL.md), and [documentation](../../../skills/anyo-docs/SKILL.md) guidance.

## Implemented API and decisions

The implementation resolves the original proposals as follows. The [public hosting contract](../../knowledge/webhook-triggers.md#embedding-and-http-extension) owns the detailed behavior and limits.

| Surface | Implemented approach |
| --- | --- |
| Default application access | `anyo.asgi(...)` uses the same registry as `anyo.add_hook(...)` and `anyo.harness(...)`. Each call creates a fresh HTTP object. Concurrent lifespans over one registry are rejected before consumer resources start. |
| Independent applications | `anyo.create_application(queue_url=...)` composes built-in providers, harnesses, and queue adapters with SQLite defaults. `anyo.Application` and `anyo.application.Application` support explicit composition with their existing memory default. |
| HTTP extension | The returned Starlette application is the extension surface for routes and middleware. Customize before startup and preserve Anyo's reserved routes and raw signed request bodies. |
| Convenience serving after extension | `anyo.serve(app, host=..., port=...)` serves the supplied ASGI callable, including a middleware wrapper or parent host. It accepts no runtime settings; configure those in `asgi(...)`. Existing `start()` uses the same serving helper after building its own app. |
| Host lifecycle | `anyo.lifespan(http)` composes a mounted child's lifecycle with its host. `asgi(lifespan=resources)` claims the registry first, enters consumer resources before Anyo starts, and closes them after Anyo drains. Optional yielded mappings support request state. |
| Mounted dashboard | Routing accounts for the ASGI root path; redirects and cookie paths include it. HTML assets and JavaScript requests use relative URLs. Login, CSRF controls, logout, and sibling session isolation remain protected. |

ASGI with the existing asyncio runtime is the supported integration contract. The [runnable example](../../../examples/http_hosting.py) demonstrates external serving, extension, lifecycle composition, and mounting with Starlette. A consumer-owned host does not invoke Anyo's blocking `start()` or `serve()`.

A transport-neutral webhook handler and WSGI adapters are not introduced. Other async backends, new server frameworks, hot reload, and arbitrary live route/registration mutation remain outside this task.

Starlette and Uvicorn remain base dependencies. No installation migration, package split, runtime dependency change, or version change is needed. The serving helper is separate from runtime configuration, avoiding ambiguous overrides of an already-built app.

## Work plan

1. Resolve the proposed public entry points with small consumer examples for external serving, mounting, and extending the convenience app. Record lifecycle/resource ownership, repeated-build behavior, and configuration precedence in the webhook contract.
2. Reuse the current app builder and package-root composition; add the smallest public API needed for default and isolated applications. Keep provider-specific logic behind existing contracts.
3. Implement lifecycle composition and serving of the customized app. Preserve registration freeze, startup errors, shutdown guarantees, and worker-only execution.
4. Make webhook and dashboard behavior work under a mount prefix, including protected browser flows and multiple isolated applications in one host.
5. Add runnable examples and a short hosting/extension guide in `docs/wiki/`, following [README/wiki guidance](../../../skills/anyo-readme-wiki/SKILL.md). Update the wiki and example indexes and affected knowledge. Keep the root README's quick start small.
6. Verify the public contracts below, including installed-package examples. Record outcomes and limits here before marking the task done.

## Completion criteria

- [x] A consumer can register hooks through package-level functions and obtain the corresponding HTTP app entirely through public APIs. Independently composed applications retain separate hooks, runtime state, and configuration.
- [x] An external ASGI host receives an authenticated webhook and runs its callback without calling `anyo.start()`. Anyo neither opens another listener nor takes over the host loop, signals, or logging.
- [x] A host-owned route and mounted Anyo webhooks both work in one application. A consumer-added route and middleware also work on the exact HTTP app served through the convenience server, including an exercised startup/shutdown callback.
- [x] Root and prefixed dashboard flows pass: login, redirects, assets, authenticated reads, a CSRF-protected action, and logout. Cookies and generated URLs stay within the correct mounted application; sibling routes/apps remain usable. UI protection and webhook authentication stay independent.
- [x] Tests cover missing lifespan, simultaneous/repeated starts, independent instances, partial startup failure, cancellation, graceful shutdown, and restart. No owned tasks/processes/resources leak, and host-owned resources remain usable after Anyo closes.
- [x] Existing provider reception and response tests pass. Focused embedding tests cover signed raw bodies, invalid authentication, capacity/error responses, deduplication, and a Slack immediate response without duplicating provider decoding or admission logic.
- [x] The existing standalone server, memory compatibility mode, default SQLite, Redis, receiver-only, and worker-only paths retain their documented behavior. Distinguish pipeline concurrency from HTTP server process count and preserve documented process-local session/responder limits.
- [x] Examples use installed public imports and demonstrate external hosting, prefix mounting with lifecycle composition, and extension of the convenience app. Applicable Ruff/mypy checks and regression tests pass; a clean wheel installation outside the checkout runs the hosting examples with packaged UI assets and `py.typed` present. Any dependency change also receives base/extra installation checks.
- [x] Documentation distinguishes shipped behavior from remaining proposals, records final API and compatibility choices, and has valid local links. Update the task and knowledge indexes when implementation is complete.

## Outcome and verification

Consumers can now expose the default registry with `anyo.asgi()`, create isolated applications, customize the HTTP app, serve that exact object, and compose its lifecycle inside an existing host. The dashboard works at a mount prefix without losing its authentication or cookie boundary. Existing startup, provider contracts, queues, and worker roles remain compatible.

Verification on 2026-10-12:

| Check | Result |
| --- | --- |
| Default regression suite | 878 passed, 41 live tests deselected. Includes all provider, queue, worker, UI, and harness fixture tests. |
| Clean installed-wheel suite | 878 passed, 41 deselected, using tests extracted from the source archive outside the checkout. |
| Real HTTP lifecycle | Standalone `start()`, customized `serve(app)`, and a consumer-owned Uvicorn listener with a mounted app all receive signed deliveries and drain on SIGTERM. Custom routes, middleware, request state, and consumer resources work through shutdown. |
| Embedding boundaries | Independent registries, duplicate/concurrent starts, missing lifecycle, failed backend/host startup, cancellation, restart, body limits, capacity, deduplication, and immediate Slack response replay pass. Borrowed sockets, event loop, signals, and logging remain host-owned. |
| Mounted UI | Contract tests pass at root, a mount, a proxy-plus-mount prefix, and a path containing a space. They exercise asset links, login, cookie scoping, protected reads, CSRF cancellation, logout, and two independent dashboard sessions. |
| Browser verification | The installed example at `/anyo/ui/` loads its assets, signs in, shows a successful signed GitHub delivery and its pipeline logs, and signs out successfully. |
| Real Redis | Against an isolated local Redis 7.4 container: 11 live checks passed, 1 container-restart check skipped, 7 non-live checks deselected. An additional installed public-API smoke verified a mounted receiver, independent worker, execution, and duplicate rejection. |
| Distribution | Built a source archive and its wheel in `dist/http-hosting-verified/`; all 64 wheel runtime files match source, including `py.typed` and UI assets. Twine metadata checks passed. |
| Base installation | Public imports and the real HTTP example work with only base dependencies, before installing HTTPX2, Redis, or pytest. Host/extended routes, middleware, protected mounted UI, signed admission, and deduplication passed. `pip check` passed before and after adding test tools. |
| Static checks | Ruff lint passed for the repository; mypy passed for 57 source files and separately for the typed hosting example. Formatting passes for source, tests, examples, and changed documentation. The whole-repository formatter still reports pre-existing fenced-code formatting in `knowledge/worker-queues.md` and `wiki/repository-deployment.md`. |

Verification used Python 3.12.14 on macOS, Starlette 1.7.0, Uvicorn 0.54.0, HTTPX2 2.13.1, and redis-py 8.1.0 where installed. Other Python/OS combinations and other ASGI server implementations were not separately exercised. The skipped Redis test requires explicit container-restart configuration; this task does not change persistence. No paid model calls, live provider configuration, package publication, or production deployment were performed. Temporary HTTP and Redis services were stopped.

The [hosting guide](../../wiki/http-hosting.md), [complete example](../../../examples/http_hosting.py), and linked knowledge describe the final API, ownership, and limits. Initial planning checked 124 local links across four documents before runtime implementation; final documentation verification covers the expanded changed-document set.

## Sources

- Project conversation, 2026-10-12 (no share link supplied): requested using Anyo as a library with a consumer-owned HTTP server, access to extend Anyo's server, and autonomous task creation without delegation or clarification.
- Same conversation, 2026-10-12: authorized continuing with implementation of the plan.
- [Application implementation](../../../src/anyo/application.py), [default composition](../../../src/anyo/__init__.py), and [dependency declarations](../../../pyproject.toml): current hosting, lifecycle, and package behavior.
- [Dashboard implementation](../../../src/anyo/ui/_server.py) and [browser client](../../../src/anyo/ui/assets/ui.js): current path and session handling.
- [Hosting tests](../../../tests/test_hosting.py), [application tests](../../../tests/test_application.py), [server tests](../../../tests/test_server.py), [UI tests](../../../tests/test_ui.py), and [Slack reception tests](../../../tests/integrations/slack/test_application.py): implemented contract coverage.
