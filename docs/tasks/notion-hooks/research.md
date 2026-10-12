# Notion webhook research

## For Humans

Notion can notify Anyo when content or related resources change. This research identifies the supported notification surface, its setup requirements, and the limits the [integration task](plan.md) needs to preserve. It separates documented platform behavior from proposed Anyo behavior and live checks still to perform.

Status: draft

Last updated: 2026-10-12

Scope: official public Notion documentation and existing Anyo contracts. No Notion account was accessed or modified.

## Event inventory

The current references enumerate 29 current event names plus two legacy database names. The general delivery overview omits several newer families, so use the [documentation index](https://developers.notion.com/llms.txt) together with individual event schemas when maintaining fixtures.

| Family | Native event names | Primary reference |
| --- | --- | --- |
| Pages | `page.created`, `page.content_updated`, `page.properties_updated`, `page.moved`, `page.deleted`, `page.undeleted`, `page.locked`, `page.unlocked` | [Page event reference](https://developers.notion.com/reference/webhooks/page-created) and its linked page-event schemas |
| Transcript removal | `page.transcription_block.transcript_deleted` | [Transcript deletion](https://developers.notion.com/reference/webhooks/page-transcription-block-transcript-deleted) |
| Databases | `database.created`, `database.moved`, `database.deleted`, `database.undeleted` | [Database events](https://developers.notion.com/reference/webhooks/database-created) |
| Data sources | `data_source.created`, `data_source.content_updated`, `data_source.schema_updated`, `data_source.moved`, `data_source.deleted`, `data_source.undeleted` | [Data-source events](https://developers.notion.com/reference/webhooks/data-source-created) |
| Comments | `comment.created`, `comment.updated`, `comment.deleted` | [Comment events](https://developers.notion.com/reference/webhooks/comment-created) |
| File uploads | `file_upload.created`, `file_upload.completed`, `file_upload.expired`, `file_upload.upload_failed` | [File completion](https://developers.notion.com/reference/webhooks/file-upload-completed), [upload failure](https://developers.notion.com/reference/webhooks/file-upload-upload-failed) |
| Views | `view.created`, `view.updated`, `view.deleted` | [View events](https://developers.notion.com/reference/webhooks/view-created) |
| Legacy database events | `database.content_updated`, `database.schema_updated` | [2025-09-03 migration](https://developers.notion.com/guides/get-started/upgrade-guide-2025-09-03); superseded by corresponding `data_source.*` events |

This inventory establishes names to receive and test. It does not prove that every event can be enabled for every account or connection. Recheck the available subscription choices and capability requirements during live verification.

## Setup and authenticity

Notion requires a reachable HTTPS endpoint. Its initial POST contains a `verification_token`; the operator enters that token in the connection UI. A resend option exists. Later event signatures use HMAC-SHA256, the token as key, and `X-Notion-Signature: sha256=<hex>`. Hash the original request bytes, not reserialized JSON. The verified URL cannot be edited without recreating the subscription. The guide establishes no signed timestamp or key-ID rotation scheme. [Webhook setup/signing](https://developers.notion.com/reference/webhooks).

Design consequence: setup and authenticated reception need separate lifecycles. An initial candidate cannot authenticate itself. The [plan](plan.md#verification-and-authentication) proposes a bounded collector and manual confirmation, without changing the generic requirement that ordinary hooks start with a secret.

## Envelope and version differences

The common envelope includes an event ID, timestamp, workspace/subscription/integration IDs, native type, authors, attempt number, and entity. The [page schema](https://developers.notion.com/reference/webhooks/page-created) marks `api_version` required although its example and older overview examples omit it. Its event ID is a string; do not require the UUID appearance used in older samples. Preserve source fields and treat omissions explicitly rather than inventing values.

The overview's entity list is incomplete. [Comment events](https://developers.notion.com/reference/webhooks/comment-created) use a comment entity and provide page/discussion context under `data`. [File completion](https://developers.notion.com/reference/webhooks/file-upload-completed) uses a file-upload entity and has no event-specific `data` requirement. Current generated [database](https://developers.notion.com/reference/webhooks/database-created) and [data-source](https://developers.notion.com/reference/webhooks/data-source-created) examples omit `entity.type`; capture live envelopes to distinguish schema omissions from actual wire behavior. Preserve compatible additive fields; a parser designed only around pages will reject valid events.

Webhook schema version is chosen in subscription settings. Since `2025-09-03`, databases contain data sources and parent context can include `data.parent.data_source_id`; the identifiers are not interchangeable. Preserve the old and new event names instead of silently translating them. [Webhook migration](https://developers.notion.com/guides/get-started/upgrade-guide-2025-09-03).

Current API guidance uses `2026-03-11`. The [2026 migration guide](https://developers.notion.com/guides/get-started/upgrade-guide-2026-03-11) says webhook payloads are unchanged from `2025-09-03`. A REST request's `Notion-Version` header does not configure an incoming subscription. The receiver needs no outbound REST client. [Versioning](https://developers.notion.com/reference/versioning).

## Delivery and access limits

Notifications can be aggregated and delivered out of order; rapid changes may collapse or produce no final notification. Delivery is usually within a minute, with guidance allowing five minutes. Failed acknowledgments trigger backoff extending to roughly 24 hours. The prose says up to eight retries while `attempt_number` is described as 1–8; do not promise an exact attempt count. Notification content is not a complete resource snapshot. [Delivery guide](https://developers.notion.com/reference/webhooks-events-delivery).

Individual schemas specify HTTP `200` acknowledgment; the reviewed sources do not establish a precise HTTP response timeout. [View-created response](https://developers.notion.com/reference/webhooks/view-created). Anyo's proposed admission boundary and five-second budget are local design choices.

Connections receive notifications only for accessible resources. Comments additionally require the read-comments capability. Anyo cannot repair missing access by accepting a webhook or knowing an object ID. [Capabilities](https://developers.notion.com/reference/capabilities), [comment webhook setup](https://developers.notion.com/reference/webhooks#test-2-add-a-comment).

If consumer code needs content, [page retrieval](https://developers.notion.com/reference/retrieve-a-page) returns metadata/properties, while [block children](https://developers.notion.com/reference/get-block-children) are paginated and nested content needs further calls. A deleted or inaccessible object may no longer be retrievable. This task intentionally leaves fetching and follow-up edits to consumers; avoid making them prerequisites for admission or deletion-event handling.

## Existing Anyo fit

| Existing component | Design implication |
| --- | --- |
| [Provider contracts](../../../src/anyo/hooks.py) | Authentication, decoding, and matching stay in the provider; no network calls in those methods. `WebhookDelivery` supplies the acknowledgment. |
| [Application](../../../src/anyo/application.py) | Normal startup requires hook secrets. Authentication is per secret, source filters run locally, and acknowledgment follows admission. A separate setup helper avoids weakening this flow. |
| [Composition root](../../../src/anyo/__init__.py) | Register the inert built-in adapter here without import-time credential resolution or operational side effects. |
| [Better Stack event codec](../../../src/anyo/integrations/betterstack/_codec.py) | Model typed, versioned transport on an existing provider-owned codec, including consistency validation of stored fields. |
| [Worker queues](../../knowledge/worker-queues.md) | Retained deduplication is per hook/provider identity; failures can be replayed, retention is bounded, and separate workers do not need signing secrets. |

Unlike payload-derived identity used where a platform lacks event IDs, Notion supplies an event ID. The plan therefore proposes identity derived from that ID and its source scope, independent of attempt metadata. Cross-subscription correlation and exact external-effect deduplication remain consumer concerns.

## Evidence still needed during implementation

- Capture a real verification exchange and confirm the proposed token-file → UI confirmation → normal receiver sequence, including resend, retry across the handoff, and failure recovery. The initial POST is not evidence of trusted origin; normal dispatch must wait for manual confirmation.
- Capture signed delivery/retry samples to verify stable event identity, actual body changes between attempts, and timeout/retry response behavior. No precise response deadline or exhaustive retryable-status policy was found.
- Check versioned envelopes and the `api_version` omission discrepancy. Record generated fixtures as local contract checks, not live evidence.
- Confirm subscription availability, capability requirements, and aggregation behavior for transcript, view, and file-upload events. Do not infer them from page events.
- Test access loss, multi-source database parent context, and events authored by the integration where available. No built-in suppression of bot-authored changes is proposed; consumers doing later writes must prevent feedback loops.

Sources above were reviewed on 2026-10-11. These gaps do not prevent designing the task; they limit what a later implementation can claim without live verification.

## Implementation coverage

The [provider tests](../../../tests/integrations/notion/test_provider.py) use documentation-derived, sanitized common envelopes for all 31 names. Family fixtures exercise absent data for file notifications, comment context, and omitted entity types in database/data-source examples. They are local compatibility fixtures, not captured deliveries or validation of every nested native schema. Future-name and malformed-input cases are explicitly synthetic.

The [final contract](../../knowledge/notion-hooks.md) preserves native names, optional source context, and source-scoped event identity. Setup, signature, worker, and installed-package verification are recorded in the [task](plan.md#implementation-verification). The initial planning request was followed by implementation authorization on 2026-10-12; no live Notion account was configured. The webhook setup, delivery, and file-completion references were rechecked during implementation.
