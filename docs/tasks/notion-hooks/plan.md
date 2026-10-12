# Start Anyo pipelines from Notion events

## For Humans

A page update, new comment, or database change in Notion can now start a Python pipeline. This task records the implemented provider, separate verification-token collector, and worker integration alongside the design rationale. Start with [Connect Notion](../../wiki/notion-hooks.md); the [final contract](../../knowledge/notion-hooks.md) describes delivered behavior and limits.

Status: done

Created: 2026-10-11

Last updated: 2026-10-12

Scope: implemented Notion webhook reception, setup verification, typed events, durable worker transport, examples, and documentation; local, installed-package, and real Redis verification complete. Live Notion delivery remains unverified.

## Objective and authorization

Deliver **Notion event → authenticated Anyo hook → durable queue → consumer pipeline** through `anyo.add_hook(...)` and `anyo.start()`.

The user requested project-context research and a Notion integration task on 2026-10-11, then selected **Notion events that trigger Anyo pipelines**. On 2026-10-12 the user authorized implementation, a new branch, and a PR, working alone. The design below is implemented unless a verification limitation is stated. No live Notion account changes were requested.

Dependencies: completed [provider organization](../provider-packages/plan.md) and [SQLite/Redis workers](../sqlite-redis-workers/plan.md). Related knowledge: [product direction](../../knowledge/product-overview.md), [webhook contract](../../knowledge/webhook-triggers.md), [worker queues](../../knowledge/worker-queues.md), and [operations UI](../../knowledge/operations-ui.md). [Official-source research](research.md) records platform evidence and remaining gaps.

## Scope

- Receive native connection webhooks at `/webhooks/notion`; cover the current event inventory and the two legacy database event names listed in the research.
- Support explicit event selection, workspace/subscription/entity filtering, synchronous and asynchronous pipelines, and the existing memory, SQLite, Redis, and separate-worker modes.
- Provide a small setup-only token collector, followed by mandatory signature validation in the normal receiver.
- Preserve native context through queue storage, retries, worker restarts, and supported process execution. Use existing run inspection and logs.
- Deliver a runnable receiver example, a short wiki setup guide, the final knowledge contract, and installed-package verification.

Exclude outbound Notion reads/writes, automatic content fetching, OAuth installation management, remote subscription provisioning, polling/backfill, database-automation “send webhook” actions, and a new workflow engine. A pipeline may use its own Notion client later. Receiving a change notification does not provide a page's complete content, property values before/after an edit, or a reliable history of every edit.

## Consumer API

This example uses the implemented API. The [runnable example](../../../examples/notion_webhook.py) also supports separate worker roles and Redis.

```python
import anyo
from anyo import HookEvent
from anyo.integrations.notion import NotionEvent


def page_changed(event: HookEvent) -> None:
    if not isinstance(event, NotionEvent):
        raise TypeError("Expected a Notion event")
    print(f"Notion {event.event_type}: {event.entity_id}")


if __name__ == "__main__":
    anyo.add_hook(
        provider="notion",
        action="event",
        event_type="page.content_updated",
        workspace_id="11111111-1111-4111-8111-111111111111",
        subscription_id="22222222-2222-4222-8222-222222222222",
        pipeline=page_changed,
        hook_id="notion-page-changed",
    )
    anyo.start(retention_seconds=172800)
```

Replace the example identifiers with the intended workspace and subscription. Resolve the signing token from `ANYO_NOTION_VERIFICATION_TOKEN`, or accept the existing per-hook `secret=` override. The token is separate from a Notion REST access token; no REST credential or SDK is needed for reception.

Use `action="event"` and a required `event_type`: one native name, or `"*"` explicitly. No implicit subscription to every event, aliases such as `page_updated`, or partial glob patterns. Accept well-formed future native names without a hardcoded event allowlist. Unknown names can match an exact registration or `"*"` when their envelope remains compatible; this does not promise support for future incompatible payload shapes. Upstream subscription settings still control which events Notion sends.

Optional exact filters are `workspace_id`, `integration_id`, `subscription_id`, `entity_id`, and `entity_type`. Combine supplied filters with AND; omission means unrestricted. Normalize documented UUID identifiers consistently in configuration and decoded context; preserve the original payload. Event/type names remain case-sensitive. Reject unknown configuration keys and malformed selectors during registration.

An entity filter matches the notification's entity only. It does not include descendants or infer database membership. In particular, `entity_id=<database id>` must not silently match every page in that database. Keep parent information and changed-property identifiers in the payload for consumer logic. Matching performs no API calls, and no built-in filter promises “status changed to Done.”

## Verification and authentication

Notion's bootstrap uses a delivered `verification_token` and manual confirmation in its UI. Subsequent events use that token for `X-Notion-Signature`. The exchange is not a challenge-echo protocol. [Notion setup and signing](https://developers.notion.com/reference/webhooks).

The current [application startup](../../../src/anyo/application.py) requires a secret before serving hooks. Preserve that contract by adding a provider-owned setup command:

```sh
python -m anyo.integrations.notion verify \
  --host 127.0.0.1 --port 8000 \
  --token-file /private/anyo/notion-verification-token
```

The setup command is a bounded, one-purpose receiver, separate from `anyo.start()`. The implemented sequence is:

1. Run it behind the consumer's public HTTPS endpoint, serving the eventual `/webhooks/notion` path. The supplied destination directory must already exist and be private.
2. Create the Notion subscription. Accept only a bounded JSON object containing a nonempty `verification_token`; treat it as an untrusted candidate. Use a 16 KiB request limit, five-second read deadline, and ten-minute default setup lifetime.
3. Save one candidate to a newly created file with owner-only access (`0600` on POSIX) and acknowledge it. Refuse existing files and symlinks; never overwrite or print the token. Keep the setup-only listener available until the operator stops it or the overall timeout expires. Repeated identical verification requests can return `200`; a different candidate must not replace the saved one. Ordinary event deliveries receive `503` for later retry. Do not create queues, run callbacks, expose a dashboard, or promote the candidate into runtime configuration automatically. Refuse insecure storage on platforms where equivalent permissions cannot be established.
4. The operator confirms the saved candidate in Notion's UI **before** configuring or starting normal dispatch. Only after confirmation succeeds does the operator stop the collector, provision the token in the normal receiver's environment, and start the intended hooks at the same URL. During this handoff the endpoint must fail or return non-success, never acknowledge discarded events. A spoofed candidate must cause setup failure, never runtime trust. Document resend/retry and secure removal or secret-manager storage of the local file.

The normal receiver always requires a configured token and rejects unsigned verification bodies. Setup's HTTP response contains no token. Report setup outcome and destination only; ensure the candidate never reaches ordinary logs, error text, queue data, or command arguments. Clean up owned sockets and incomplete output on timeout, bind failure, write failure, and cancellation. Preserve a successfully written candidate if acknowledgment fails so recovery is explicit.

For normal events, compute HMAC-SHA256 over the original bytes and compare the `sha256=<hex>` value in constant time before parsing JSON. Reject missing, malformed, repeated, or mismatched signature headers. Authenticate independently for each hook's secret through the existing application flow; a token for one subscription must not authorize hooks using another token. Do not invent a freshness window from the event timestamp or allow a bootstrap body to replace a secret. Token rotation follows explicit configuration and restart; this task adds no automatic rotation protocol.

## Event, routing, and delivery contract

Export `NotionProvider` and `NotionEvent` from `anyo.integrations.notion`. Keep the package-root API small. `NotionEvent` extends `HookEvent` with the following context:

| Field | Meaning |
| --- | --- |
| `event_id`, `event_type` | Native event identity and exact `type`; the ID is an opaque, bounded nonempty string. |
| `workspace_id`, `integration_id`, `subscription_id` | Validated source identifiers used for local filtering and identity. |
| `entity_id`, `entity_type` | Native subject ID and type; use `None` for a type omitted by a compatible envelope. Support comments, file uploads, views, and future well-formed types as well as pages/databases/data sources. |
| `timestamp`, `attempt_number` | Source timestamp and positive integer transport attempt; neither changes delivery identity. |
| `api_version` | Version reported by the envelope, or `None` when omitted in a compatible legacy payload. Never guess it. |
| `payload` | Private copy of the complete finite-JSON notification, including authors, access context, and event-specific data when present. |

Set base `provider="notion"` and `action="event"`; leave GitHub-specific fields at their existing defaults. Preserve additional JSON keys without requiring every event family to have a `data` object. Validate required common fields and the types of optional fields when present. Some current database/data-source examples omit `entity.type`; preserve that absence rather than guessing from the event name, and ensure it cannot satisfy an exact `entity_type` filter. Reject duplicate JSON keys, non-finite numbers, malformed identifiers, and invalid structures consistently in memory and durable modes. Do not impose the documentation's illustrative attempt maximum as a hard validation limit.

Keep native `database.*` and `data_source.*` names and identifiers distinct. Test subscriptions rendered as `2022-06-28`, `2025-09-03`, and `2026-03-11`; use `2026-03-11` in the initial setup guide, subject to rechecking at implementation. Accept additive fields and a well-formed unfamiliar `api_version` if the envelope still validates. [Webhook version migration](https://developers.notion.com/guides/get-started/upgrade-guide-2025-09-03) and [current version guidance](https://developers.notion.com/reference/versioning).

Derive a versioned `delivery_id` from the canonical tuple `(workspace_id, integration_id, subscription_id, event_id)`, using an unambiguous encoding and digest. Exclude timestamp, attempt number, signature, and mutable payload data. Retain `event_id` separately for consumers. This scopes duplicate suppression to a subscription: overlapping subscriptions may cause separate runs for one underlying change. Do not create a second deduplication store or claim cross-subscription exactly-once behavior.

Return an empty HTTP `200` through `WebhookDelivery` after confirmed admission, or for an authenticated notification with no matching jobs or retained successful duplicates. In memory mode, acceptance remains process-local. Reuse atomic fan-out, `400` malformed-event responses, `401` authentication failures, body limits, and `503` capacity/storage/deadline responses. Use the existing response-provider deadline mechanism with a five-second total local budget; that is an Anyo choice, not a verified Notion timeout.

Reject Notion `responder=` registrations immediately. Today, the structural `ResponseProvider` contract couples total deadlines to accepting responders. Add a small optional, provider-independent capability `ResponderCapability` with `supports_responders=False`, checked during registration; absence preserves existing response-provider behavior. Notion opts out, and existing providers/custom extensions keep their contracts. Document and test this generic distinction rather than adding a provider-name branch or deferring failure until delivery.

Pipelines execute after admission and their failures use existing replay policy. Failed/interrupted redelivery reuses the original admitted event; `attempt_number` is not the worker attempt counter and need not show the latest HTTP retry. Preserve successful sibling suppression, secret-free worker startup, and explicit `RetryPolicy` semantics.

The example's 48-hour terminal retention is a buffer around the documented roughly day-long upstream retry period. Anyo's global one-hour default remains unchanged. Size `max_records` and storage for the chosen traffic and retention: a full history returns `503`, and an expired record can execute again. Explain that upstream aggregation, reordered events, and bounded retention preclude a complete edit history or exactly-once external effects. [Delivery evidence](research.md#delivery-and-access-limits).

## Implementation steps and ownership

1. **Fix the evidence baseline.** Recheck the [research inventory](research.md#event-inventory), save minimal sanitized fixtures for each native name, and distinguish official shapes from synthetic future/error cases. Resolve the listed platform gaps where a test subscription is available.
2. **Implement the provider.** Add `src/anyo/integrations/notion/` with adapter, configuration/decoding, event, and codec responsibilities split only as needed. Use the standard library for JSON and HMAC. Wire the inert adapter in `src/anyo/__init__.py` and add the generic responder-capability registration guard to the owning hook/application contracts. Do not add Notion branches to HTTP reception, hooks, execution, or storage adapters.
3. **Implement setup.** Keep `__main__.py` and the private collector in the Notion package, reusing installed Starlette/Uvicorn. Ensure setup works without a hook registry, signing token, queue, or optional SDK.
4. **Connect durable transport.** Supply `anyo.notion.v1` through the provider's `event_codec`. Follow the [Better Stack codec](../../../src/anyo/integrations/betterstack/_codec.py): reconstruct the trusted event class from payload, validate derived fields, and never import classes named in stored data. Register the same codec in receiver and worker processes through existing composition.
5. **Verify and document.** Add `tests/integrations/notion/`, `examples/notion_webhook.py`, `docs/wiki/notion-hooks.md`, and the final `docs/knowledge/notion-hooks.md` when implemented. Update their indexes, project status, and support statements to actual results. Keep the root README thin. Use the repository's [Python](../../../skills/anyo-python/SKILL.md), [structure](../../../skills/anyo-structure/SKILL.md), [testing](../../../skills/anyo-testing/SKILL.md), and [packaging](../../../skills/anyo-packaging/SKILL.md) guidance.

## Verifiable completion criteria

- [x] The installed consumer example receives a signed page event and executes its callback through the default SQLite runtime. Each researched native event name has a routing fixture, including comment entities, file events without `data`, transcript deletion, views, and both legacy database names.
- [x] Setup succeeds without credentials or queues and cannot run pipelines. Tests cover its exact envelope, body/deadline bounds, concurrent requests, repeated/different candidates, ordinary-event rejection, existing file/symlink refusal, permissions, write/acknowledgment failure, timeout, cancellation, and listener cleanup. Manual confirmation precedes runtime trust; candidate secrets are absent from replies, logs, and diagnostics.
- [x] Normal receiver startup fails without a token; worker-only startup needs none. Raw-byte HMAC tests cover Unicode and whitespace, tampering, wrong secrets, ambiguous headers, and per-hook secret isolation. Unsigned setup requests cannot dispatch or replace configuration.
- [x] Exact and wildcard selection, combined source/entity filters, missing context, UUID normalization, legacy/current version shapes, unknown additive fields/types, malformed JSON, and private fan-out payload copies behave as documented. Signed deletion events run without fetching the resource. Import, registration, decode, and matching perform no network access.
- [x] Retries with changed attempt metadata retain delivery identity; distinct event IDs and subscriptions remain distinct. Concurrent duplicates, restart, terminal expiry, failed-sibling replay, and optional worker retries retain existing semantics without a second store.
- [x] Acknowledgment follows confirmed storage admission. Capacity/storage failure, ambiguous commit recovery, atomic multi-hook admission, and deadlines are tested; a slow callback does not delay acknowledgment. Unmatched authenticated events return `200` without execution. Notion responders fail at registration, while existing and custom response providers retain their supported responder behavior.
- [x] Typed context and opaque payload fields survive SQLite and Redis transport, separate receiver/worker interpreters, restart, and supported process execution. Include an explicitly selected real Redis check and a worker without receiver secrets. Existing provider and generic extension tests remain green.
- [x] Event/payload representations and routine logs omit private content and authentication material; the existing dashboard can inspect resulting runs without provider-specific UI changes.
- [x] Relevant pytest, Ruff, and mypy checks pass. Build a source archive and wheel, inspect contents, and verify imports, setup command, example, and consumer typing from a clean base-dependency installation outside the checkout. No Notion SDK or new extra is required.
- [x] The wiki explains public HTTPS, manual verification, source access/capabilities, subscription version and selection, multiple subscriptions, retention, aggregation/order, and content-fetching boundaries. New Markdown links work. Record local, installed-package, real-service, and live Notion results separately; mark unavailable live checks unverified.

## Implementation outcome

- Added the [provider package](../../../src/anyo/integrations/notion/__init__.py), strict raw-byte signature validation, native context, exact filters, subscription-scoped identity, and the `anyo.notion.v1` codec.
- Added a POSIX setup-only collector with exclusive private-file creation, retryable event responses, explicit manual trust, and bounded cleanup.
- Added the generic responder opt-out without provider-specific core branches; existing providers and custom extensions retain their behavior.
- Added the [runnable example](../../../examples/notion_webhook.py), [wiki guide](../../wiki/notion-hooks.md), and [accepted contract](../../knowledge/notion-hooks.md). No dependency, version, or release-policy changes were needed.

## Implementation verification

- **858 tests passed, 41 live tests deselected**, against a fresh installed wheel outside the source checkout on macOS / Python 3.12.14. The focused Notion and response-provider run passed **144 tests**, with two live tests deselected.
- **Two explicitly selected Redis 7.4 tests passed** against the installed wheel, using an isolated local container: separate receiver/worker interpreters, thread/process callbacks, typed context, retry metadata, and replacement-worker deduplication. Test namespaces and the temporary container were cleaned up.
- Before adding test dependencies, the clean base installation used **Starlette 1.7.0 and Uvicorn 0.53.0**, the declared lower bounds. Imports resolved to `site-packages` with no HTTPX2 or Redis SDK present. The installed setup command saved a candidate over real local HTTP without exposing it, and the actual example admitted a signed event and completed its SQLite callback.
- Ruff checks and formatting passed for `src`, `tests`, and `examples`; strict mypy passed for all **57 source modules**, the runnable example, and an installed consumer assigning the provider to the public provider/response/capability/codec protocols.
- Built a fresh source archive and then its wheel through isolated build environments. Twine and `pip check` passed. All **64 runtime/source-resource files** in the wheel match the checkout; seven Notion modules and `py.typed` are present. The source archive includes the Notion tests and subprocess worker fixture. Version and dependency metadata are unchanged; no package was published.
- Checked the changed Markdown openings and **221 local links/anchors**, plus syntax of five Notion Python/shell snippets. The documentation describes setup's manual trust boundary and distinguishes deterministic fixtures from live platform evidence.

Artifacts: [wheel](../../../dist/notion-hooks-verified/anyo-0.1.0.dev0-py3-none-any.whl) and [source archive](../../../dist/notion-hooks-verified/anyo-0.1.0.dev0.tar.gz). Live Notion subscriptions, new-family availability/capabilities, actual retry captures, and account-specific setup remain unverified. No paid model calls or production deployment were performed. Python versions/platform combinations beyond the recorded environment were not exercised.

Before claiming live compatibility, exercise manual verification and a signed delivery with a designated test connection. Confirm retry identity, actual subscription-version envelopes, setup acknowledgment expectations, and availability/capabilities of newer event families. Deterministic local fixtures remain required even if a live workspace is unavailable; lack of live access must be reported rather than replaced with a success claim.

## Sources

- User conversation, 2026-10-11 (no share link supplied): requested context from project docs and design of a Notion integration task; confirmed webhook-triggered pipelines as the scope.
- [Notion research](research.md), reviewed 2026-10-11, and the linked project contracts and source code establish the design inputs. The final contract and verification above describe the delivered choices.

- User follow-up, 2026-10-12 (no share link supplied): authorized implementation, a new branch, and a PR, working alone.
