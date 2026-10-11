# Better Stack hooks

## For Humans

Better Stack incidents and error notifications can start ordinary Anyo pipelines, with the same queue and worker behavior as other providers. This contract explains routing, authentication, event data, and delivery limits. Start with [Connect Better Stack](../wiki/betterstack-hooks.md) for the runnable setup.

Status: accepted  
Last updated: 2026-10-11  
Scope: implemented inbound incident/error provider and local verification; account-specific live delivery remains unverified. The [task](../tasks/betterstack-hooks/plan.md) records implementation evidence and [research](../tasks/betterstack-hooks/research.md) separates platform facts from gaps.

## Registration

The default application includes `provider="betterstack"`. Isolated applications can explicitly compose `BetterStackProvider` from `anyo.integrations.betterstack`. Importing or registering performs no network operations. No new dependency or SDK is required.

| Option | Contract |
| --- | --- |
| `action` | `incident` or `error`. |
| `event_type` | Required, case-sensitive native event name, or explicit `*` for every name within the family. Names may contain lowercase letters, digits, `_`, `.`, `:`, and `-`, and start with a letter. Future names are accepted without an enum update. |
| `resource_id` | Optional exact incident/error-group ID. |
| `monitor_id` | Incident-only exact filter; incidents without a monitor do not match it. |
| `team_id`, `application_id` | Error-only exact filters. Incident team context is not inferred from names or metadata. |
| `environment` | Error-only, case-sensitive exact string. It has no wildcard semantics. |
| `secret` | Optional per-hook secret; otherwise resolved from `ANYO_BETTERSTACK_WEBHOOK_SECRET` at receiver startup. |
| `hook_id`, `hook_version`, `retry` | Existing stable identity, namespace compatibility, and retry contracts. Use explicit IDs in deployment examples. |

IDs accept positive integers or nonempty strings of up to 256 ASCII letters/digits and `_`, `.`, `:`, `-`, starting with a letter/digit. Integers normalize to strings. Missing context fails an exact filter. Unknown options and options belonging to the wrong family fail at registration. The event selector has a 128-character maximum; environment filters have a 255-character maximum.

Use `pipeline=` for a sync or async callback. No immediate business responder is supported: a matching `responder=` has no response renderer and fails reception with `400`; use a pipeline instead. Ordinary callback return values are discarded.

## Payload selection and event context

The envelope requires an object `data` with `type`, `id`, and object `attributes` for supported families. Incident event names come from the top-level `event`; errors use `data.attributes.event`. Preserve native names without translating guessed spellings. A missing incident `event` maps to the Anyo sentinel `unspecified`; explicit null/invalid names fail. Missing error event names fail. Timestamp state does not imply which transition occurred.

This accommodates the documented incident template that omits the event discriminator. Select desired lifecycle changes in Better Stack and use `event_type="*"` when that template is used. Exact native selectors work when your payload supplies the corresponding name. The [coverage record](../tasks/betterstack-hooks/research.md#implementation-coverage) distinguishes documented wire values from synthetic lifecycle tests.

Callbacks receive `BetterStackEvent`, a `HookEvent` subclass exported by `anyo.integrations.betterstack`, with:

- `provider`, family `action`, `event_type`, `resource_id`, and `delivery_id`.
- Optional error `team_id`, `application_id`, `environment`, `source_url` from `error_url`, `first_seen`, and `last_seen`.
- Optional incident `monitor_id`, `comment_id`, `started_at`, `acknowledged_at`, and `resolved_at`.
- The complete JSON `payload`, including unknown optional attributes, comments, metadata, and error details. Each callback receives a separate payload copy. GitHub repository/PR fields remain empty/absent.

Monitor IDs come only from `data.relationships.monitor.data` with type `monitor`; absent relationships or null relationship data are allowed. A `comment` notification requires a top-level comment object with ID, `incident_comment` type, and attributes. Source timestamp strings are preserved, with null/empty strings becoming `None`; they do not establish event ordering. The monitored incident `url` is not mislabeled as an incident permalink.

Other resource types, including monitors and releases, are acknowledged without dispatch. The documented on-call shape (`data.calendar_id` and `data.message`, without a resource type) is also ignored. This is not support for monitor/on-call/release pipelines. Status-page subscriptions and arbitrary custom schemas are outside the contract. Test deliveries receive ordinary handling; no undocumented test marker is assumed.

## Authentication and HTTP behavior

Send POST JSON to `/webhooks/betterstack`. Set `X-Anyo-Webhook-Secret` in Better Stack to the Anyo hook secret. Use HTTPS. Secrets must be printable ASCII without whitespace or commas and at most 4096 characters; long random hex is recommended. Missing/empty secrets fail receiver startup; other unusable secret formats fail authentication. Per-hook secrets remain isolated, and worker-only startup requires none.

The provider uses constant-time byte comparison before JSON decoding. This is shared-secret authentication, not a body signature or replay-freshness check. Basic authentication, API tokens, and URL/query secrets are not accepted as alternatives. Repeated HTTP header values are joined with comma-space by the generic receiver, making repeated credentials invalid. This also prevents choosing an arbitrary first GitHub/Slack signature header. Proxies must reject ambiguous credentials or preserve them; Anyo cannot detect duplication already discarded upstream.

The receiver preserves existing body-size limits. Decoding rejects duplicate JSON keys, invalid UTF-8/surrogates, non-finite numbers, malformed context, and nesting deeper than 64 levels. Durable serialization also applies its envelope/depth/size limits. No outbound fetch or user pipeline runs during decoding.

| Result | HTTP response |
| --- | --- |
| Queue admission confirmed; duplicate; authenticated ignored/unmatched event | Empty `200`, text/plain. |
| Missing/wrong/ambiguous secret | `401`, safe authentication error. |
| Malformed supported envelope or unsupported serialization | `400`. |
| Wrong content type | `415`. |
| Oversized body | `413`. |
| Body-read timeout | `408`, unless the total provider deadline expires first. |
| Queue unavailable/full or total deadline expired | `503`; no successful admission is claimed. |

The provider's total deadline defaults to five seconds. An explicitly composed `BetterStackProvider(request_timeout=...)` accepts a finite positive value below 25 seconds; the application body-read limit also applies. Better Stack's incident timing policy is not established by local verification. A queue commit whose acknowledgment is lost remains potentially successful: a retry with the same identity is safe within the existing queue policy.

## Identity, retries, and worker behavior

No verified upstream notification ID is available for both families. `delivery_id` is `betterstack:payload:v1:` plus SHA-256 of the entire parsed payload, encoded as sorted, compact, UTF-8 JSON. Whitespace and object-key ordering do not change identity. All payload fields participate, including family, native event, resource IDs, source context, timestamps, and comments where present. Header credentials and request receipt time do not participate. Numeric `1` and `1.0` remain different JSON encodings.

This fallback suppresses identical retries and preserves notifications with distinguishing payloads. It cannot distinguish two legitimate notifications with identical JSON, and a retry whose body changes can create another job. Whole resource IDs, error last-seen times, or incident status alone are not reliable notification identities. Exactly-once processing or lossless transition delivery is not promised.

SQLite/Redis scope deduplication by stable hook and namespace, persist it through restarts, and allow explicit redelivery of failed/interrupted work under the [worker contract](worker-queues.md). Retention expiry permits another run. Memory mode has process-local history. Separate receivers/workers, thread/process execution, operations controls, and bounded Anyo retries use existing contracts; Better Stack delivery retry policy remains separate.

Direct error and derived incident notifications intentionally remain independent. There is no cross-surface correlation or ordering guarantee. Consumers that fetch current incident state or perform external effects do so in their callback, using their own credentials and idempotency rules.

## Privacy and verification limits

The provider does not log secret headers or full bodies. Event representations omit payload, environment, and source URL; exception responses are sanitized. Durable queues retain full private payloads so workers can investigate: secure their storage and retention accordingly. Consumer logging or prompts can expose data the consumer explicitly passes onward.

The provider was exercised with documentation-derived and synthetic fixtures, real local HTTP, isolated SQLite/Redis, and separate workers. No live Better Stack account was configured. Unknown native spellings, retry-body stability, incident retry/timeouts, and account notification policies remain explicit platform verification limits. Source payloads do not guarantee a full stack trace, and Anyo does not enrich them automatically.

## Sources

- User authorization, 2026-10-11 (conversation, no share link supplied): implement and test the [task](../tasks/betterstack-hooks/plan.md), working alone without clarification.
- [Official-source research](../tasks/betterstack-hooks/research.md), reviewed on 2026-10-11, and the linked implementation tests establish the respective platform and Anyo behavior.
