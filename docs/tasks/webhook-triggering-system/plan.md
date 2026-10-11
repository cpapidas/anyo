# Design and implement webhook triggers

## For Humans

A developer installs Anyo into a Python project, connects events to pipeline functions, and runs the project's main file to start listening. When a GitHub pull request is created, Anyo should select the matching hooks and run their pipelines. This task designs that triggering system first, then implements GitHub as its first provider while leaving room for more trigger providers.

Status: done

Implementation contract: [webhook triggers](../../knowledge/webhook-triggers.md). The detailed engineering choices were recorded there before runtime implementation and updated to match the delivered behavior.

## Objective and scope

Deliver the first usable webhook triggering flow through `anyo.add_hook(...)` and `anyo.start()`. The task covers hook registration, provider configuration, webhook reception, event matching, pipeline dispatch, runtime lifecycle, and an installed-library example.

Related knowledge: [product direction](../../knowledge/product-overview.md). Apply the [structure](../../../skills/anyo-structure/SKILL.md), [Python](../../../skills/anyo-python/SKILL.md), [testing](../../../skills/anyo-testing/SKILL.md), and [packaging](../../../skills/anyo-packaging/SKILL.md) guidance to the implementation, and the [documentation guidance](../../../skills/anyo-docs/SKILL.md) to its design records.

## Confirmed requirements

- The consumer installs Anyo as a library in an existing Python project.
- The consumer registers hooks in code before calling `anyo.start()` in the main file.
- A hook binds a provider event and its required configuration to a pipeline callable.
- Support multiple registered hooks and design for additional trigger providers. Implement GitHub first, with the action name `pr_created` and the configuration fields `organization`, `repository`, and `pipeline`.
- Running the main file starts the application through `anyo.start()` with useful runtime defaults. The consumer should not have to assemble a separate HTTP application or launch a separate server command for the default flow.
- Use webhooks for this implementation. The earlier suggestion to start with GitHub Actions is superseded.
- Design the triggering contracts before implementing them. The APIs are now implemented; the planning sections below retain the original design scope, and the outcome records what was delivered.

### Intended consumer API

The requested call pattern, expressed with Python keyword syntax:

```python
import anyo
from pipelines import pipeline_function, another_pipeline_function

anyo.add_hook(
    provider="github",
    action="pr_created",
    organization="your-org",
    repository="repo_name",
    pipeline=pipeline_function,
)

anyo.add_hook(
    provider="github",
    action="pr_created",
    organization="your-org",
    repository="another_repo",
    pipeline=another_pipeline_function,
)

if __name__ == "__main__":
    anyo.start()
```

`pipelines` represents the consumer's own module. Both callbacks accept a `HookEvent`; their return values are discarded. The [completed example](../../../examples/README.md) includes the secret and webhook setup prerequisites omitted from this compact registration example.

`add_hook` describes what should run when an event matches. `start` activates reception and dispatch for the registered hooks; it does not immediately execute every pipeline.

## Design work before implementation

The questions below guided the design before implementation. They are now resolved for this version in the [webhook contract](../../knowledge/webhook-triggers.md), which distinguishes engineering choices from deferred capabilities.

| Area | Decisions to resolve |
| --- | --- |
| Hook registration | Typed signature and return value; validation timing; hook identity; unknown providers/actions; missing or unexpected configuration; non-callable pipelines. |
| Multiple hooks | Whether one event invokes every matching registration; exact duplicate registration behavior; invocation order; isolation when one pipeline fails. Do not let a later registration silently overwrite an earlier one. |
| Provider configuration | How provider-specific fields are validated while preserving the simple call pattern; shared versus per-hook secrets; multiple repositories or organizations with different credentials; whether personal repository owners are supported by `organization`. |
| Event and pipeline contract | Callable signature; event/context fields such as delivery ID, repository, PR number, and head commit SHA; provider-specific payload access; supported sync/async callables; treatment of return values and exceptions. |
| Startup and shutdown | Default address, port, and webhook path; configuration overrides; blocking behavior of `start()`; no-hook startup; repeated starts; registration after startup; interrupt handling and cleanup of owned resources. |
| GitHub setup | Repository webhooks versus a GitHub App; manual versus automatic webhook registration; required secret and any API credentials; public callback URL; local development forwarding and deployed HTTPS setup. |
| Execution and delivery | How requests hand work to pipeline execution; concurrency and capacity limits; timeouts; duplicate deliveries; redelivery after partial failure; retry policy; in-flight work on shutdown; persistence and restart guarantees. |
| Library setup | Python baseline, build and dependency tooling, HTTP runtime, and base versus optional GitHub dependencies needed for the installed example. Preserve a small import surface and avoid unrelated scaffolding. |

Starting a listener does not itself make a local computer reachable from GitHub. The design must explain what `anyo.start()` handles automatically and what one-time external setup is required. If automatic webhook registration is selected, define permissions, idempotent setup, and ownership of any remote resources; do not create or delete remote webhooks merely by importing the library. GitHub delivers events to the URL configured for the webhook. [GitHub delivery guide](https://docs.github.com/en/webhooks/using-webhooks/handling-webhook-deliveries).

### Proposed capability boundaries

- The hook capability owns registrations, validation contracts, matching, and provider extension interfaces. Keep generic routing independent of GitHub payload fields.
- The GitHub adapter belongs under `src/anyo/integrations/` and translates GitHub deliveries into the agreed trigger contract. Concrete adapters implement the generic interfaces; core capabilities do not import concrete providers.
- Startup owns the listener and resources it creates. Pipeline execution consumes matched registrations and event data through a defined dispatch boundary; hook definitions must not depend on an execution engine.
- Keep `anyo.add_hook` and `anyo.start` as deliberate public exports. Importing `anyo` must not start listeners, execute pipelines, or contact providers.
- Resolve the owning modules during design, following the existing capability layout. If a new hook capability is introduced, update the structure guidance as part of implementation. Do not build unused provider plugins or a full workflow engine to demonstrate the first trigger.

### GitHub behavior to cover

- Translate Anyo's `pr_created` action to the GitHub `pull_request` event with payload action `opened`. Match the configured organization and repository against the event's target repository, including PRs whose source branch is in a fork. Other PR actions must not trigger this hook. [GitHub event reference](https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request).
- Validate the `X-Hub-Signature-256` HMAC-SHA256 signature against the original request body and the configured secret before dispatch, using a constant-time comparison. Invalid or missing signatures must not execute pipelines. [GitHub signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
- Handle setup `ping` events, irrelevant events, malformed payloads, and unmatched repositories explicitly. Define HTTP responses independently of pipeline outcomes.
- Acknowledge accepted deliveries promptly without waiting for a potentially long pipeline. GitHub requires a response within 10 seconds. Specify when work counts as accepted and what happens if dispatch capacity is exhausted. [GitHub webhook practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks).
- Use delivery identity in the duplicate/redelivery design and track outcomes per matched hook so that suppressing a duplicate does not suppress other intended pipelines. GitHub preserves `X-GitHub-Delivery` on redelivery. Document the chosen retention and restart limits rather than promising exactly-once execution. [GitHub delivery identifiers](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks#use-the-x-github-delivery-header).

## Implementation sequence

1. Resolve and document the design questions, with concrete examples for registration, configuration, callback inputs, startup, and failure behavior.
2. Establish only the packaging and runtime foundation needed for this slice. Implement registration, matching, and the provider interface through the agreed public imports.
3. Implement GitHub verification and event translation, then connect webhook reception to pipeline dispatch and lifecycle management.
4. Add a runnable consumer example and setup instructions covering installation, secrets, the callback URL, GitHub configuration, startup, and shutdown.
5. Verify the public behavior and built package, record the outcomes, and update the documentation index and product direction to reflect the delivered contract.

## Completion criteria and verification

- The design record resolves the table above and distinguishes guaranteed behavior from explicitly deferred capabilities.
- In a clean consumer project using the installed package, registration followed by `anyo.start()` starts the documented default runtime. A valid matching GitHub PR-created delivery invokes the intended pipeline with the agreed input.
- Multiple hooks route independently by provider, action, organization, and repository. Tests cover multiple matches, exact duplicate registration policy, different target repositories, fork PRs, and unrelated actions.
- Deterministic tests cover invalid signatures, malformed or incomplete payloads, setup pings, unknown provider/action configuration, missing secrets, and non-callable pipelines. None of these cases accidentally invokes a pipeline.
- A controlled slow pipeline does not block delivery acknowledgment. Tests cover pipeline failure, capacity limits, duplicate delivery, redelivery after partial failure, and shutdown according to the selected execution contract, without timing-dependent sleeps or claims beyond the documented guarantees.
- Startup and cleanup tests cover binding failures, the selected repeated-start policy, and resource release. Importing the package has no operational side effects.
- A small fake provider exercises the extension boundary without modifying GitHub routing or requiring a second real integration. Default tests require no provider credentials or external network access; any live GitHub verification is selected explicitly and reported separately.
- Build and inspect the source distribution and wheel, install the wheel in a clean environment, and verify public imports and the local webhook flow outside the checkout. Check optional dependency behavior if the design introduces extras.
- User-facing documentation contains a complete setup and run example, selected defaults, delivery guarantees, and actionable startup errors. Record actual commands and outcomes when implementation completes; this plan alone is not evidence of working runtime behavior.

## Scope limits

Only GitHub `pr_created` is implemented initially. Other providers, additional GitHub actions, schedules, polling, and GitHub Actions integration remain future work. Full agent execution, script runners, prompt optimization, automatic PR checkout, and publishing a package are outside this task; the callback can use the consumer's own code. The first implementation uses in-memory state and manual webhook registration; durable storage and automatic remote provisioning are deferred.

## Outcome and verification

Delivered `anyo.add_hook(...)`, `anyo.start()`, isolated `Application` instances, typed hook/provider contracts, raw-body GitHub signature verification, target-repository routing, bounded background execution, per-hook deduplication and failed-hook redelivery, and graceful draining. Added the runnable GitHub example, setup guide, package metadata, and structure guidance for the implemented capabilities.

Verification on 2026-10-03 with Python 3.12.14, Starlette 1.7.0, and Uvicorn 0.53.0:

- `.venv/bin/python -m pytest -q`: 147 passed, including local real-server startup, signed HTTP delivery, prompt acknowledgment while a callback waits, SIGTERM draining, and port release.
- `.venv/bin/ruff check src tests examples` and `.venv/bin/ruff format --check src tests examples`: passed.
- `.venv/bin/mypy`: passed for all six checked source files.
- `.venv/bin/python -m build`: built the source distribution, then built the wheel from that archive in isolated environments. `.venv/bin/python -m twine check dist/*` and dependency consistency checks passed.
- Inspected wheel contents for the single `anyo` package and `py.typed`, and inspected the source archive for rebuild inputs and absence of local environments/secrets.
- Installed the wheel with only its runtime dependencies into a fresh virtual environment outside the checkout. Verified public imports, typing marker, metadata, and a signed webhook through the real ASGI application without pytest or HTTPX2 installed. Installed test tools afterward and reran the full suite against that wheel: 147 passed, including the real-server subprocess test.
- Read-only review found a nested-payload copy failure that could produce HTTP 500. Added rejection before admission and a regression check proving a later valid delivery still runs.

The artifacts are `dist/anyo-0.1.0.dev0.tar.gz` and `dist/anyo-0.1.0.dev0-py3-none-any.whl`. No package was published and no remote webhook was created. Live GitHub delivery and Python versions beyond the tested 3.12 environment were not exercised. Delivery state remains process-local and non-durable, as documented.

## Sources

- Project conversation, 2026-10-03 (no share link supplied): user selected webhooks, requested a triggering-system design, specified GitHub as the first provider, described `anyo.add_hook(...)` followed by `anyo.start()`, and requested this implementation task.
- [Product direction](../../knowledge/product-overview.md): installable Python library, programmable pipelines, and the motivating GitHub workflow.
- GitHub documentation linked above, checked on 2026-10-03, supplies provider constraints rather than selecting Anyo's runtime architecture.
