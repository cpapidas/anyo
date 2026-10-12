# Notion hooks

## For Humans

Notion notifications can start Python pipelines through Anyo's existing queues and workers. This contract explains verification, native event selection, typed context, and delivery limits. Start with [Connect Notion](../wiki/notion-hooks.md) for setup and a runnable example.

Status: accepted

Last updated: 2026-10-12

Scope: inbound connection webhooks and a separate token collector. The [task](../tasks/notion-hooks/plan.md) records verification; [research](../tasks/notion-hooks/research.md) records platform evidence and live gaps.

## Registration and ownership

The default application includes `provider="notion"`. Advanced applications can compose `NotionProvider` from `anyo.integrations.notion`. Import, registration, decoding, and matching perform no network operations. Reception needs no REST token, SDK, or new dependency.

| Option | Contract |
| --- | --- |
| `action` | `event`. |
| `event_type` | Required exact native name, or explicit `*`. Names contain at least two dot-separated segments, each starting with a lowercase letter and containing lowercase letters, digits, or underscores; at most 255 characters total. No partial globbing or aliases. |
| `workspace_id`, `integration_id`, `subscription_id`, `entity_id` | Optional exact UUID filters, combined with AND. Hyphenated or 32-digit hexadecimal forms normalize to lowercase hyphenated UUIDs. |
| `entity_type` | Optional exact native type, at most 64 lowercase letters/digits/underscores, starting with a letter. Missing source type never matches. |
| `secret` | Existing per-hook override; otherwise receiver startup reads `ANYO_NOTION_VERIFICATION_TOKEN`. Use the token already confirmed in Notion. |
| `hook_id`, `hook_version`, `retry` | Existing stable identity, namespace compatibility, and bounded worker replay options. |

`pipeline=` accepts sync or async callbacks receiving a `HookEvent`; narrow it to `NotionEvent` for native context. `responder=` is rejected at registration. Callback return values are discarded. Unknown options, invalid UUIDs, or unsupported actions fail immediately.

Exact or wildcard subscriptions accept future native names when their common envelope remains compatible. This does not automatically subscribe to new upstream events. An entity filter selects that specific entity, not its descendants. Database IDs and data-source IDs stay distinct. Filtering does not fetch parent information or inspect property values to infer transitions such as “status became Done.” Consumers can inspect native `data` or fetch content inside their own pipelines.

## Typed event and payload

`NotionEvent` extends `HookEvent` with `event_id`, `event_type`, normalized `workspace_id`, `integration_id`, `subscription_id`, `entity_id`, optional `entity_type`, `timestamp`, `attempt_number`, and optional `api_version`. The provider/action are `notion`/`event`; GitHub repository and PR fields remain empty/absent.

The event ID is an opaque nonempty string of at most 512 characters without control characters. Timestamps must parse as ISO 8601 with time and timezone. Attempts are positive integers, excluding booleans; no upstream maximum is hardcoded. Present API versions must be valid `YYYY-MM-DD` dates, including future dates. Omitted versions and entity types remain `None`. Names and opaque source strings are preserved; UUID context alone is normalized.

`payload` contains the complete native JSON, including original ID spellings, authors, optional access lists, and family-specific data. Each callback gets a private copy. The common envelope requires `authors` as a list of ID/type objects; `accessible_by` is validated similarly when present. `workspace_name` is an optional string. A present `data` must be an object, but file events may omit it. Additional JSON fields and actor/entity types are preserved. The provider deliberately does not claim to validate every event-specific `data` schema.

The [31-name fixture inventory](../tasks/notion-hooks/research.md#event-inventory) covers current native types and two legacy database types. Current generated database/data-source examples omit `entity.type`; compatibility accepts that omission without guessing. Older samples omit `api_version`; compatibility does not infer a version. Subscription version is selected in Notion independently of REST client configuration.

JSON decoding rejects duplicate keys, invalid UTF-8/surrogates, non-finite numbers, invalid common fields, and nesting deeper than 64 levels. Queue serialization adds its existing envelope limits. Signed bodies containing a top-level `verification_token` are rejected instead of persisting credentials. Signatures and receiver configuration are never inserted into events.

## Authentication and HTTP responses

POST JSON to `/webhooks/notion`. Verify `X-Notion-Signature: sha256=<lowercase hex>` with HMAC-SHA256 over the original bytes and constant-time comparison, before parsing. The key is the confirmed verification token. Repeated headers are rejected, including identical repetitions; proxies must preserve them or reject ambiguity themselves. No query token, REST credential, challenge echo, or unsigned fallback is accepted. Each hook authenticates with its own secret; worker-only startup requires none.

| Outcome | Response |
| --- | --- |
| Admission confirmed, retained duplicate, or authenticated unmatched event | Empty `200`. |
| Missing, malformed, repeated, or incorrect signature | `401`. |
| Malformed envelope/JSON or unsupported queue serialization | `400`. |
| Wrong content type | `415`. |
| Body too large | `413`. |
| Body-read timeout | `408`, unless the total provider deadline expires first. |
| Queue full/unavailable or total deadline exceeded | `503`. |

`NotionProvider(request_timeout=5)` applies a positive finite total local request/admission budget. Pipeline execution is outside this budget. The value is Anyo policy, not a verified Notion timeout. A durable `200` follows confirmed admission; memory-mode acceptance remains process-local. Atomic fan-out, ambiguous-commit recovery, and safe errors follow the [shared contract](webhook-triggers.md).

## Setup-only token collection

`python -m anyo.integrations.notion verify --token-file /private/directory/candidate` starts a standalone listener. Defaults are host `127.0.0.1`, port `8000`, and lifetime 600 seconds; `--host`, `--port`, and positive finite `--timeout` override them. Each body has a five-second read deadline and a 16 KiB size limit. Normal events receive `503`, never dispatch or successful acknowledgment.

The collector requires a pre-existing POSIX directory owned by the running user with no group/other access. It pins that directory, rejects a symlink as the final directory component, and exclusively creates a regular `0600` file. It refuses pre-existing files/symlinks. Platforms without the required permissions are refused. Use local filesystem storage; this small setup helper performs its bounded file write synchronously.

The only accepted candidate envelope is `{"verification_token": "..."}` with 1–4096 printable non-whitespace ASCII characters. The first complete file write is acknowledged; identical candidates can repeat, and different candidates get `409`. Partial failed writes are removed when still owned. A completed candidate survives an acknowledgment failure, timeout, or shutdown. Restarting with an existing destination fails; retain it for verification or choose a fresh file for an intentional retry.

No token appears in replies or ordinary logs/output. The command reports only the destination and instructions. It remains active until interrupted or its lifetime expires, then closes its listener and directory handle. A lifetime expiry without any candidate exits unsuccessfully. This command cannot prove origin or complete verification: the operator must confirm the candidate in Notion's UI **before** configuring or starting ordinary dispatch. After confirmation, stop the helper, load the token securely, and start the normal receiver at the same URL. Keep failed deliveries retryable during handoff. A spoofed candidate can cause setup failure but must never be promoted automatically.

## Identity, retention, and workers

`delivery_id` is `notion:event:v1:` plus SHA-256 of the JSON-encoded array `[workspace_id, integration_id, subscription_id, event_id]`. The three source UUIDs are canonical; the native event ID is unchanged. Encoding uses Python's default JSON separators and ASCII escaping. Timestamp, payload edits, attempt count, and signatures do not alter identity. Different subscriptions intentionally remain independent, even if they describe the same underlying edit.

SQLite/Redis retain per-hook deduplication across restarts; memory history stays local. Successful siblings are not repeated when a failed sibling is redelivered. The original admitted event is replayed, so its HTTP attempt number may differ from the newest incoming request and from the worker attempt counter. `anyo.notion.v1` reconstructs the trusted event class and checks derived fields against the payload; queued data never selects an importable class.

Anyo's default terminal retention remains one hour. The example uses 48 hours as a buffer around Notion's documented roughly day-long retries; size `max_records` and storage for traffic during that period. A full retained history returns `503`; expiration, backend/namespace changes, or overlapping subscriptions can permit another run. Aggregation and unordered delivery mean notifications cannot provide a complete edit history. No exactly-once external effects, automatic ordering, or missed-event backfill is promised. [Worker contract](worker-queues.md).

## Privacy and verification boundaries

Representations omit raw payloads and the provider does not log content or credentials. Durable storage still contains private event data for workers; existing operations controls and retention apply. Consumer code can expose content it explicitly logs or passes to models. Deletion notifications run without fetching an object that may no longer exist. REST reads/writes, OAuth, and subscription provisioning remain consumer responsibilities.

Local tests establish Anyo's behavior, not a live Notion account's event availability or delivery timing. See the [task verification](../tasks/notion-hooks/plan.md#implementation-verification) for executed checks and [research gaps](../tasks/notion-hooks/research.md#evidence-still-needed-during-implementation) for the remaining manual platform checks.

## Sources

- User conversation, 2026-10-11: selected Notion events triggering pipelines; 2026-10-12: authorized implementation on a new branch and a PR, working alone.
- [Official-source research](../tasks/notion-hooks/research.md), rechecked during implementation, and the [implementation task](../tasks/notion-hooks/plan.md).
