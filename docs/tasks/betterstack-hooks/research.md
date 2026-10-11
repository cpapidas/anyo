# Better Stack hook research

## For Humans

An incident or application error should be able to start an Anyo pipeline automatically. Better Stack has separate outgoing webhook surfaces for these signals. This research identifies the relevant sources, the existing Anyo extension points, and the details that need verification during the [implementation task](plan.md).

Status: draft  
Last updated: 2026-10-11  
Scope: public Better Stack documentation and local Anyo contracts; no account configuration, live deliveries, or provider implementation.

## Verified platform capabilities

| Surface | Officially documented behavior | Consequence for this task |
| --- | --- | --- |
| Incident outgoing webhooks | POST notifications for creation, acknowledgment, resolution, reopening, and comments. Configure under Incidents → Integrations → Exporting data. [Incident webhooks](https://betterstack.com/docs/uptime/webhooks/). | Receive incident lifecycle notifications directly. |
| Error outgoing webhooks | Select new errors, thresholds, spikes, recurrence, resolution, ignoring, and unresolving. Configure under Errors → Outgoing webhooks; a test-send control is available. [Error webhooks](https://betterstack.com/docs/errors/integrations/outgoing-webhooks/). | Receive errors without requiring incident creation. These are lifecycle notifications, not a documented stream of every exception occurrence. |
| Log and metric alerts | Chart alerts create incidents; configuration includes thresholds, percentage changes, anomalies, per-series incidents, and confirmation/recovery periods. [Telemetry alerts](https://betterstack.com/docs/logs/dashboards/alerts/). | Proposed route: alert → incident → incident webhook → Anyo. This combines documented capabilities; the complete route still needs verification. |
| Error-to-incident integration | Selected error groups can automatically create incidents, with team or escalation-policy routing. [Reporting incidents](https://betterstack.com/docs/errors/integrations/creating-incidents/). | An alternative for teams that already manage errors as incidents. Enabling both routes can produce two independent triggers for one underlying problem. |
| Other outgoing surfaces | Monitor changes and on-call changes have their own webhooks. Error tracking also offers release webhooks. [Incident webhooks](https://betterstack.com/docs/uptime/webhooks/), [error webhooks](https://betterstack.com/docs/errors/integrations/outgoing-webhooks/). | Exclude these from the initial task. |
| Status-page subscriptions | A separate subscription protocol publishes public status updates and documents its own authentication and delivery policy. [Status-page subscriptions](https://betterstack.com/docs/uptime/status-pages/subscribing-to-status-updates/subscribing-with-webhooks/). | Do not reuse its payload, headers, or retry policy for internal incident hooks. |

Better Stack's incoming webhooks create incidents inside Better Stack. The direction needed here is its outgoing webhooks calling Anyo. [Manual incident creation](https://betterstack.com/docs/uptime/creating-incidents-manually/).

## Payload and authentication evidence

The error example uses `data.type="error"`, a group identifier in `data.id`, and `data.attributes.event`. It includes application/team IDs, environment, error URL, message/type, call site, and first/last-seen times. The guide explicitly names `new_error`, `threshold`, and `reoccurred`; confirm all other wire spellings before freezing the API. Requests are unsigned; custom secret headers and Basic authentication are supported. The documented timeout is 25 seconds, with up to three retries. [Error webhook contract](https://betterstack.com/docs/errors/integrations/outgoing-webhooks/).

The incident API documents custom headers, Basic authentication, and request templates. Its default template contains `data.type="incident"`, an incident ID, and incident attributes. A separate comment example includes a top-level `event="comment"`, a monitor relationship, and a comment object with its own ID and timestamps. The displayed default template omits `event`, so it does not establish all actual delivery discriminators. Incident control flags use `on_incident_started`, `on_incident_acknowledged`, `on_incident_resolved`, `on_incident_reopened`, and `on_incident_comment`; those configuration names are not evidence of exact wire values. [Update outgoing webhook](https://betterstack.com/docs/uptime/api/update-outgoing-webhook-integration/).

Incident metadata templates have distinct v2/v3 behavior; v3 changes metadata rendering, not the entire webhook schema. Avoid requiring custom metadata for basic admission. [Metadata template versions](https://betterstack.com/docs/uptime/webhooks/).

## What the reviewed documentation does not establish

- A unique notification/delivery ID for both surfaces, stable across retries. An incident ID or error-group ID identifies a resource, not each transition.
- Whether repeated attempts reuse identical bodies or refresh mutable attributes. A payload hash cannot resolve this uncertainty.
- Exact incident request timeout, retry schedule, retryable response codes, ordering, or a signing scheme. The error guide's timeout and retry count must not be generalized to incidents.
- A complete incident lifecycle payload inventory, including manual/Telemetry incidents without a monitor, repeated reopenings, and the exact placement of team context.
- A reliable test-delivery marker, complete error-state event spellings, or a transition timestamp for every error event. First/last-seen times describe errors and must not automatically be treated as notification creation times.
- Account-specific permissions, selected escalation behavior, and whether a configured webhook is invoked immediately for every intended source. The incident API exposes notification policy options; configuration must be verified alongside payloads.

These are implementation verification items, not questions that prevent creating the task. Use sanitized captures from an explicitly selected test integration or more complete official references. Never fill gaps by copying the status-page protocol or assuming GitHub/Slack signature conventions.

## Existing Anyo integration points

Local inspection on 2026-10-11 found:

- [Provider and event contracts](../../../src/anyo/hooks.py): `validate`, `decode`, and `matches`; configuration normalizes to strings. `HookEvent` permits empty repository context. Provider-owned subclasses can supply `EventCodec` transport.
- [HTTP application](../../../src/anyo/application.py): routes `POST /webhooks/{provider}`, authenticates per hook secret, matches both action and configuration, and admits jobs before acknowledging. A `WebhookDelivery` can supply a simple `200` reply. `ResponseProvider` adds a total deadline without requiring a consumer responder.
- [Default composition](../../../src/anyo/__init__.py): GitHub and Slack are registered here. Better Stack can follow this pattern without adding provider branches to HTTP handling or workers.
- [Slack codec](../../../src/anyo/integrations/slack/_codec.py) and [shared serialization](../../../src/anyo/execution/_codec.py): preserve typed event fields across durable workers with versioned finite JSON.
- [Worker contract](../../knowledge/worker-queues.md): delivery identity is scoped by namespace, hook fingerprint, provider, and provider delivery ID. Retention is bounded; failed/interrupted redelivery can execute again. Exactly-once external effects are not promised.

One shared wording assumption needs attention: the provider protocol and generic `401` message currently describe signatures. Better Stack authentication should be described generically where shared, while retaining the existing signature requirements inside GitHub and Slack.

## Recommended direction and limits

Add one `betterstack` provider with separate incident and error actions. Use a custom shared-secret header over HTTPS and the existing per-hook secret mechanism. Keep the original payload private and preserve native identifiers; leave enrichment and follow-up operations to consumer pipelines. No Better Stack SDK or API client is needed merely to receive a webhook.

For deduplication, first look for an evidenced immutable notification identity. Otherwise use a documented, versioned identity derived from resource scope, native lifecycle event, available transition identity, and a canonical payload fingerprint. This is a proposal with limits: changed retry bodies may run twice, while identical distinct transitions may collapse during retention. Never use only the resource ID, generate random IDs per attempt, or promise lossless deduplication without upstream evidence.

No live Better Stack account was accessed, and no incident, webhook, or notification was created. The sources above were reviewed on 2026-10-11. The initial user conversation requested independent research and task creation; a subsequent message authorized implementation and testing.

## Implementation coverage

The implemented [contract](../../knowledge/betterstack-hooks.md) preserves valid native event names without an allowlist. Local fixtures in [provider tests](../../../tests/integrations/betterstack/test_provider.py) establish routing behavior, not previously undocumented Better Stack wire spellings.

| Case | Evidence and implemented behavior |
| --- | --- |
| Incident default template without `event` | Official default template; maps to the explicit Anyo value `unspecified`. Exact lifecycle names are never guessed from timestamps. |
| Incident comments | Official example supplies `comment`; validates comment identity and retains its data. |
| Incident lifecycle names | Synthetic `started`, `acknowledged`, `resolved`, and `reopened` fixtures plus an unfamiliar future name all route. Confirm actual integration spellings before exact filtering; use upstream event selection and `*` otherwise. |
| Error discriminator | Official example/description establishes `new_error`, `threshold`, and `reoccurred`. Synthetic `spike`, `resolved`, `ignored`, and `unresolved` fixtures and an unfamiliar name establish generic routing. Their exact live spellings remain unverified. |
| Manual/Telemetry incident shape | Fixtures omit monitor relationships or use null monitor data. Both work; no live alert-to-incident chain was triggered. |
| Delivery identity | Versioned canonical whole-payload hash; no unsupported upstream delivery ID or retry-field exclusion is assumed. Changed retries and identical distinct transitions retain the documented ambiguity. |
| Test callbacks | Ordinary authenticated handling; no undocumented marker suppresses a pipeline. |

The [create webhook API](https://betterstack.com/docs/uptime/api/create-outgoing-webhook-integration/) and error webhook guide were rechecked during implementation on 2026-10-11. They did not resolve the remaining incident timing/discriminator gaps. No remote service configuration or paid model calls were made.
