# Support Slack hooks across events and interactions

## For Humans

Anyo already connects GitHub webhooks to Python pipelines. Developers need the same ability for Slack activity, including workspace events, commands, and interactions that require an immediate response. This task records the delivered Slack provider, its shared runtime changes, and evidence of coverage within Slack's platform limits.

Status: done  
Created: 2026-10-03  
Last updated: 2026-10-03  
Scope: delivered Slack inbound HTTP hooks, shared response contracts, tests, examples, and consumer documentation.

## Objective and authorization

Support Slack as the next hook provider across the Events API, app-owned commands, and app interactions, preserving native event names and payloads instead of limiting users to a small event enum. Preserve GitHub behavior and the registration/startup flow.

The user requested independent research and task creation on 2026-10-03, then authorized implementation. [Research](research.md) established platform constraints; [implementation decisions](design.md) selected the contracts before coding. The [coverage inventory](coverage.md) reconciles 153 official catalog entries and the separate command/interaction surfaces. The [consumer guide](../../knowledge/slack-hooks.md) describes the delivered API.

Dependencies: the completed [GitHub trigger task](../webhook-triggering-system/plan.md) and [provider package task](../provider-packages/plan.md). Related knowledge: [product direction](../../knowledge/product-overview.md) and [shared webhook contract](../../knowledge/webhook-triggers.md). Repository structure, Python, testing, packaging, and documentation skills were applied.

## Delivered behavior and affected components

| Component | Outcome |
| --- | --- |
| [Slack provider package](../../../src/anyo/integrations/slack/__init__.py) | Standard-library signing verification, strict JSON/form decoding, native context, case-sensitive app/installation filters, message aliases, multiple-action matching, bot/self filters, and provider-owned response validation. No SDK or new runtime dependency. |
| [Shared contracts](../../../src/anyo/hooks.py) | Optional repository context preserves positional event construction. Added `HookResponse`, `WebhookDelivery`, `WebhookResponse`, `Responder`, and the structural `ResponseProvider` extension. `SlackEvent` owns native fields. |
| [Application](../../../src/anyo/application.py) | Optional async `responder=`; one response owner; per-hook secret validation; provider-specific acknowledgments through generic contracts; total deadlines; atomic background admission after the response decision. No Slack branches. |
| [Execution](../../../src/anyo/execution/__init__.py) | Separate bounded immediate-response concurrency and replay cache; concurrent duplicate coalescing; cancellation ownership; retention and failure retry. Late admission after synchronous payload copying is rejected atomically. Existing background execution remains compatible. The link follows its later promotion to a package. |
| [Default application](../../../src/anyo/__init__.py) | GitHub and Slack wired at the composition root; importing and registering still require no credentials or network activity. |
| [Slack tests](../../../tests/integrations/slack/) and [shared response tests](../../../tests/test_responses.py) | Signed offline fixtures, full HTTP routing, response validation, security boundaries, identity, failures, capacity, deadlines, and compatibility. |
| [Example](../../../examples/slack_webhook.py) and [minimal manifest](../../../examples/slack-app-manifest.json) | Mentions, reactions, command reply with a button and external select, and dynamic menu options. Only example scopes are requested. |
| [Setup/API guide](../../knowledge/slack-hooks.md) and [inventory](coverage.md) | Installation, permissions, subscriptions, request URLs, filters, callback context, immediate responses, delivery guarantees, and unavailable surfaces. Shared knowledge and indexes updated. |

## Coverage and response policy

- `action="event"` receives arbitrary authenticated native event types and subtypes, including future names. Message subscriptions normalize to `message` and a conversation filter. The 121 catalog entries marked Events-compatible have parameterized signed HTTP routing cases; these use synthetic envelopes, not captured live deliveries.
- `action="slash_command"` receives app-owned commands and supports an immediate message response. Commands retain their invocation context and response URL in the private payload.
- `action="interaction"` covers Block Kit actions, global/message shortcuts, modal submission/closure, modern option loads, conditional legacy messages/dialogs/options, and other native interaction names with an empty acknowledgment. Current workflow function callbacks use their native Events API path. Consumer code owns outbound Slack calls and workflow completion.
- `action="control"` exposes envelope-level delivery notices such as `app_rate_limited`. Signed URL verification and SSL probes respond automatically and never reach business pipelines. Token-only legacy probes are rejected.
- Options requests require a matching responder. Modal/dialog errors suppress all background work for the rejected submission. Successful immediate replies are replayed if background admission later fails, preventing the responder from repeating on that retry.

The default Slack route is `POST /webhooks/slack`, its secret variable is `ANYO_SLACK_SIGNING_SECRET`, and its total request budget is 2.5 seconds. Per-hook secrets and isolated `Application` instances remain available. Ordinary sync/async pipelines keep their existing behavior and discarded return values.

## Completion criteria and evidence

| Criterion | Evidence |
| --- | --- |
| Exhaustive catalog accounting without an event allowlist | Dated [153-entry inventory](coverage.md), offline [snapshot](../../../tests/integrations/slack/catalog_snapshot.py), and [catalog routing tests](../../../tests/integrations/slack/test_catalog.py); each source's transport/scopes are recorded separately from runtime support. |
| Authenticate before parsing; reject ambiguous input | Signature/timestamp boundaries, tampering, wrong secrets, duplicate keys/fields, malformed JSON/forms/Unicode/numbers, body size and deadline tests in [provider tests](../../../tests/integrations/slack/test_provider.py) and [HTTP tests](../../../tests/integrations/slack/test_application.py). |
| Preserve installation and app boundaries | Exact and wildcard selectors, organization installs, null teams, shared-channel context, paired authorizations, legacy app-ID opt-in, nested actors, and bot/self behavior covered locally. |
| Handle every supported callback/response family | Modern and legacy options/groups, command messages, modal errors/update/push/clear, dialogs, shortcuts, buttons, closure, unknown native names, and setup/control cases covered. |
| Keep response ownership and execution bounded | Conflict, missing responder, independent concurrency, timeout/cancellation, retention/history limits, replay, overload, partial failure, restart, private payloads, and late synchronous admission tests. |
| Preserve GitHub and consumer provider compatibility | Existing GitHub tests remain passing, mixed-provider HTTP tests pass, a third-party response provider is exercised, and a legacy event-or-None provider passes strict consumer typing. |
| Verify installed-library behavior | Built source archive and wheel, inspected runtime files and `py.typed`, checked metadata, installed outside checkout, exercised signed Slack/GitHub HTTP handling, and ran the full offline suite against the installed wheel. |
| Explain configuration and honest guarantees | Example, manifest, setup guide, inventory, response/deadline caveats, and process-local retry limits are documented. Live verification is explicitly distinguished below. |

## Verification results

Run on 2026-10-03 using Python 3.12.14 on macOS arm64:

- `.venv/bin/python -m pytest -q`: **381 passed**; includes all existing GitHub tests and 121 catalog routing cases.
- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed.
- `.venv/bin/mypy`: passed for all 15 source modules. Strict mypy also passes for the runnable Slack example.
- `.venv/bin/python -m build --outdir dist/slack-hooks-verified`: built the source distribution, then the wheel from that archive in isolated build environments. `.venv/bin/python -m twine check dist/slack-hooks-verified/*`: both passed.
- Artifact inspection: wheel contains all 15 runtime Python modules and `py.typed`, with no tests or development artifacts. Source archive includes build inputs and all ten test/support Python files, including the catalog snapshot. Distribution version remains `0.1.0.dev0`, Python remains `>=3.12`, and runtime dependencies remain Starlette and Uvicorn.
- Installed the wheel in a fresh virtual environment outside the checkout with **Starlette 1.7.0 and Uvicorn 0.53.0**, their declared lower bounds. Before installing test tools, a base-only smoke check confirmed inert imports, installed-package resolution, the typing marker, signed Slack command/response handling, signed GitHub admission, and completed pipelines for both providers.
- After installing pytest/HTTPX2 separately, ran `python -I -m pytest -q` from the extracted source distribution, with `PYTHONPATH` removed and no editable install: **381 passed** against `site-packages`. The suite includes a real local GitHub listener, signed HTTP delivery, graceful SIGTERM draining, and port release. `pip check` passed.
- Strict installed-consumer typing verifies a pre-existing event-or-None provider, `Provider`/`ResponseProvider` assignments, old positional `HookEvent` construction, narrowed Slack context, responder registration, and the example.
- After the final README edit, rebuilt with `.venv/bin/python -m build --outdir dist/slack-hooks-final` and reran `twine check`: both passed. All wheel runtime files match the tested installation and current source byte-for-byte. Reinstalled this final wheel and repeated the signed Slack/GitHub smoke successfully. Final artifacts: [wheel](../../../dist/slack-hooks-final/anyo-0.1.0.dev0-py3-none-any.whl) and [source archive](../../../dist/slack-hooks-final/anyo-0.1.0.dev0.tar.gz).
- Checked changed Markdown opening structure, local links/anchors, and Python snippet syntax, plus the runnable example and manifest JSON. Replaced three stale references to absent historical task files with links to their existing skill guidance.

## Limits and remaining product work

No live Slack workspace was connected, no app was created or installed, and no credentials were required for verification. The example manifest was checked locally and against the documented schema, not submitted to Slack's authenticated validation API. Local routing fixtures do not establish that every conditional subscription is available to a particular workspace.

Only Python 3.12.14 and the runtime dependency versions above were exercised in a clean environment. Other supported Python versions and operating systems were not tested here. No package publication was requested or performed.

Socket Mode, Audit Logs polling, RTM-only signals, retired workflow steps, outbound Web API implementations, automatic OAuth/provisioning, and durable execution remain outside this inbound HTTP task. Arbitrary native events can route, but Slack permissions, membership, features, and plan still determine what is delivered. Detailed Block Kit field validation remains Slack's responsibility. Async responders must respect cancellation and avoid blocking the event loop; callback side effects still require consumer idempotency.

## Sources

- User conversation, 2026-10-03 (no share link supplied): requested comprehensive Slack hook planning, then instructed implementation without further questions.
- [Official-source research](research.md), [implementation decisions](design.md), and [dated coverage inventory](coverage.md).
- Local code, fixtures, and recorded commands above establish implementation behavior; official Slack sources establish platform constraints.
