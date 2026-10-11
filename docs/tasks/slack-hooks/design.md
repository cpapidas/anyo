# Slack hook implementation decisions

## For Humans

Slack sends notifications that can run in the background and interactions that need immediate response data. Anyo exposes both through hook registration while keeping Slack protocol details in its provider package. These decisions describe the implementation and preserve GitHub and third-party provider compatibility.

Status: draft  
Last updated: 2026-10-03  
Scope: engineering decisions for the authorized [Slack task](plan.md), informed by [research](research.md).

## Public contracts

- Use `provider="slack"` and actions `event`, `interaction`, `slash_command`, and `control`. Select native names with `event_type`, `interaction_type`, or `command`; `*` matches any received name in its family. Message subscription names normalize to received `message` plus a conversation-type filter. A subtype filter of `""` selects events without a subtype; omission selects all subtypes.
- Require `app_id` and at least one of `team_id` or `enterprise_id`. Explicit `*` installation selectors opt into any authorized installation. Use case-sensitive IDs and distinguish receiving installation context from a remote actor's workspace. Matching never performs network calls.
- `SlackEvent` extends `HookEvent` with optional native identifiers, timestamps, context, action descriptors, and retry metadata. Existing positional `HookEvent` construction and fields stay compatible; repository fields default to empty strings and are unused for Slack. The payload remains complete and private per invocation.
- Add `responder=` to `add_hook`. It must be an async callable accepting one event and returning `HookResponse(body=..., dispatch=True)`. A responder-only hook may omit `pipeline`. Ordinary pipelines keep their current behavior and discarded return values. At most one matching responder may own an incoming request; conflicting responders fail before either runs.
- Extend `Provider.decode` to allow `WebhookDelivery`, alongside its existing event/None results. This carries a transport-neutral acknowledgment and an optional provider-owned response renderer. Existing adapters require no new methods. A `ResponseProvider` extension advertises a total request deadline and response-aware decoding. Hosting interprets these contracts without provider branches.

## HTTP and response handling

Use `/webhooks/slack` for all Slack request URLs and `ANYO_SLACK_SIGNING_SECRET` as the default app secret. Authenticate bytes and reject signed timestamps more than 300 seconds from the clock. JSON is for Events API/control requests; form bodies contain either a JSON interaction payload or flat command/probe fields. Reject ambiguous duplicate keys and malformed encodings.

The Slack provider has a 2.5-second total request budget, including body reading and immediate response work, leaving time for HTTP transmission within Slack's three-second deadline. Async responders must not block the loop and must respect cancellation. They run independently of background workers, with a separate concurrency bound equal to `workers`; no waiting queue is created for new response work. A timed-out responder is cancelled and remains owned until it exits, so cancellation-resistant code still consumes a slot and can delay shutdown.

Response results are cached per responder hook and delivery ID, coalescing concurrent duplicates. Completed results expire using the existing retention setting; a separate `max_records` bound applies to this cache. Failed/cancelled responses may be attempted on redelivery. Successful responses remain cached even if background admission fails, so a retry can admit the pipeline without repeating a completed immediate action. Response side effects remain consumer-owned and must be idempotent.

Options requests require a matching responder. Modal submission without a responder means accept/close; modal validation errors force `dispatch=False`. Other responders may suppress dispatch explicitly. Validate JSON response envelopes, modal actions, and options structure in the Slack adapter. All matching background pipelines are admitted atomically after the response decision; overload returns failure rather than an acknowledgment of dropped work. Setup challenges never dispatch. Only signed command SSL probes are acknowledged; legacy token-only probes cannot authenticate business work.

## Delivery identity

Event callbacks use app-scoped `event_id`. Rate-limit notices use app/workspace/minute. Other callbacks use a digest of the canonical decoded payload, excluding the deprecated verification token. Distinct trigger IDs, action timestamps, view hashes/state, or command invocation fields yield distinct deliveries; retry headers never create new identities. If a payload has no `trigger_id`, top-level `action_ts`, or action-entry `action_ts`, also include the signed request timestamp as a fallback. Identical payloads without those discriminators inside that timestamp second are inherently ambiguous; a newly signed retry in another second can execute again. This limit is documented without promising exactly-once behavior.

## Dependencies and boundaries

Use the standard library for signing, JSON, forms, and matching; no Slack SDK dependency is needed. Consumers own OAuth, app installation, Slack subscriptions, outbound calls, response URLs, modal opening, and workflow completion. HTTP callback coverage includes conditional legacy interactions; Socket Mode, Audit Logs polling, retired workflow callbacks, and RTM-only signals remain explicitly outside this transport. The coverage inventory records source availability separately from local parsing/routing verification.

## Sources

- [Research and official Slack references](research.md).
- [Current provider and execution contracts](../../knowledge/webhook-triggers.md).
- User authorization to implement the Slack task, 2026-10-03 (no share link supplied).
