# Slack hook contract and setup

## For Humans

Anyo can start your Python pipeline when Slack delivers an event, a command, or an interaction with your app. Most work runs in the background, while an optional async responder handles interactions that need an immediate answer. This guide covers installation, registration, the response contract, and the limits of Slack's visibility and delivery model.

Status: draft  
Last updated: 2026-10-04  
Scope: implemented HTTP Slack provider and the shared immediate-response extension. [Task and verification](../tasks/slack-hooks/plan.md), [coverage inventory](../tasks/slack-hooks/coverage.md), and [research sources](../tasks/slack-hooks/research.md).

## Run the example

Install the checkout into a Python 3.12 or later virtual environment, following the [root guide](../../README.md). No Slack SDK is required. Then configure the example:

```sh
export ANYO_SLACK_APP_ID='A0123456789'
export ANYO_SLACK_TEAM_ID='T0123456789'
export ANYO_SLACK_SIGNING_SECRET='your-app-signing-secret'
python examples/slack_webhook.py
```

The first two environment variables are read by [the example](../../examples/slack_webhook.py); `ANYO_SLACK_SIGNING_SECRET` is Anyo's default secret setting. Get the app ID and signing secret from your Slack app's Basic Information settings and supply the target workspace ID. Keep credentials out of source control. Per-hook `secret=` overrides the environment value; only providers with registered hooks require secrets at startup.

The example handles mentions, reactions, `/anyo`, a button click, and an external menu's option request. It prints event type and workspace ID; replace `pipeline` with your work. Running it starts the existing listener at `127.0.0.1:8000`, with Slack at `/webhooks/slack` and GitHub available on its existing path when registered.

## Connect the Slack app

1. Create a Slack app. Use the [minimal example manifest](../../examples/slack-app-manifest.json) as a configuration reference, replacing every public-host placeholder; only the example's scopes are requested. You may configure it manually while the receiver is being set up.
2. Forward a publicly reachable HTTPS URL to the local listener, preserving bytes and headers. Set the signing secret and start the example before Slack verifies the Events API Request URL.
3. Enable Event Subscriptions and set the Request URL to `https://your-public-host/webhooks/slack`. Anyo verifies and answers Slack's setup challenge. Subscribe to bot events `app_mention` and `reaction_added` with `app_mentions:read` and `reactions:read` permissions.
4. Enable Interactivity & Shortcuts. Set its Request URL and Options Load URL to the same endpoint. Create `/anyo` with that endpoint and the `commands` permission.
5. Install the app into your workspace, reinstall when adding permissions, and invite its bot to a channel used for testing. Mention the bot or add a reaction there. Run `/anyo`, then click the response's button; each should invoke the corresponding pipeline.
6. Open the response's project menu to exercise `block_suggestion` and load filtered options from the responder. Creating modals or other UI, opening them using Slack API tokens, and completing workflow functions are consumer-owned operations.

Slack controls which events the installation can see. A bot's presence in one channel grants no access to every private channel. Some catalog entries require user subscriptions, administrator permissions, special features, or particular plans. Anyo does not provision Slack apps, run OAuth, install the app, subscribe remotely, or create your public HTTPS URL. See the [official Events API setup](https://docs.slack.dev/apis/events-api/), [interactivity setup](https://docs.slack.dev/interactivity/handling-user-interaction/), [command setup](https://docs.slack.dev/interactivity/implementing-slash-commands/), and [manifest reference](https://docs.slack.dev/reference/app-manifest/).

## Register hooks

```python
import anyo
from anyo import HookEvent
from anyo.integrations.slack import SlackEvent


def pipeline(event: HookEvent) -> None:
    assert isinstance(event, SlackEvent)
    print(event.event_type, event.channel_id)


anyo.add_hook(
    provider="slack",
    action="event",
    app_id="A0123456789",
    team_id="T0123456789",
    event_type="message",
    subtype="message_changed",
    pipeline=pipeline,
)
```

All registrations require `app_id` and at least one of `team_id` or `enterprise_id`. IDs match case-sensitively. Set an installation selector to `"*"` only when intentionally receiving from every authorized installation of that app; the field must still be present in the delivery. For an organization installation with a null workspace, specify `enterprise_id` and omit `team_id`.

| Action | Primary selector | Other selectors |
| --- | --- | --- |
| `event` | `event_type`, default `"*"` | `subtype`, `channel_type`, `reaction`, `callback_id`, `include_bots`, `ignore_self`, `ignore_user_id` |
| `interaction` | `interaction_type`, default `"*"` | `callback_id`, `action_id`, `block_id`, `options_request`, `allow_legacy_app_id` |
| `slash_command` | `command`, default `"*"` | Common scope and context filters |
| `control` | `event_type`, default `"*"` | Common scope and context filters; e.g. `app_rate_limited` |

All families accept `channel_id` and `user_id` filters in addition to the required app/installation configuration. A filter requiring a missing field does not match. Types, subtypes, command names, callback IDs, and reactions accept exact values or `"*"`; action and block IDs are exact. No regex or glob patterns are evaluated. Unknown options and invalid values fail at registration. Boolean options take Python booleans and normalize internally to strings for the existing provider contract.

Omit `subtype` to match all subtypes; `subtype=""` selects subtype-free events. `event_type="message.channels"`, `message.groups`, `message.im`, and `message.mpim` normalize to native `message` plus the corresponding `channel_type`. The retired `message.app_home` alias also parses for compatibility. No normalization is needed for a new native event name. Catch-alls match only received payloads; they do not enable subscriptions or override permissions.

`include_bots=False` excludes payloads with a bot ID or `bot_message` subtype. `ignore_self=True` filters message/mention payloads whose source app ID is this app; when Slack omits that source ID, configure `ignore_user_id` with the bot's user ID to exclude it explicitly. Bot events are included by default. `action_id` and `block_id` must match the same element of an action array. Each matching hook runs once with the complete array, even if several elements match it.

Installation matching prefers receiving `context_*` fields, then authorization pairs, then outer workspace/enterprise fields. It never uses the remote actor's workspace as installation authorization, and it never mixes a team from one authorization with an enterprise from another. The original envelope and authorization list remain available in the payload.

## Callback context

Callbacks receive a `SlackEvent`, which is a frozen subclass of `HookEvent`. Existing `HookEvent` positional arguments remain compatible; `organization` and `repository` default to empty strings and are unused for Slack. Shared callbacks can accept `HookEvent` and narrow with `isinstance`, as above.

Slack context includes `event_type`, `subtype`, `app_id`, `team_id`, `enterprise_id`, `installations`, `channel_id`, `channel_type`, `user_id`, `event_ts`, `message_ts`, `thread_ts`, `callback_id`, `command`, `reaction`, `bot_id`, `source_app_id`, `actions`, `options_request`, `retry_num`, and `retry_reason`. Fields remain absent where Slack provides no value. Timestamps stay strings. `user_id` uses a supplied user, edit actor, or nested message user when available; it is not a promise that every event identifies the person who caused it. `team_id` is outer context; `installations` is the receiving context used for routing.

`payload` contains the complete decoded Slack envelope or form data, including unknown fields, modal state, temporary credentials, response URLs, and workflow context. Each pipeline and responder gets an independent copy. Durable queues serialize the complete event, including typed actions/installations, and retain its first admitted snapshot on redelivery while history is retained. Do not log entire payloads or store temporary tokens unnecessarily. Anyo's default errors and logs omit payloads, credentials, response bodies, and callback exception messages.

## Immediate responders

Ordinary `pipeline=` callbacks keep their existing sync/async background behavior and discard return values. Add an async `responder=` when Slack needs immediate data. It takes one `HookEvent` and returns `HookResponse`; responder-only registrations may omit `pipeline`.

```python
from anyo import HookEvent, HookResponse


async def options(event: HookEvent) -> HookResponse:
    return HookResponse(
        {"options": [{"text": {"type": "plain_text", "text": "Anyo"}, "value": "anyo"}]},
        dispatch=False,
    )
```

Register this with `action="interaction", interaction_type="block_suggestion"` and the app/workspace and action ID. Options requests require a matching responder; missing responders return 503. For modal validation:

```python
from anyo import HookEvent, HookResponse


async def validate_submission(event: HookEvent) -> HookResponse:
    # Inspect event.payload["view"]["state"]["values"] in consumer code.
    return HookResponse({"response_action": "errors", "errors": {"email": "Enter a valid email"}})
```

Use `interaction_type="view_submission"` and the view's `callback_id`. A modal error response always suppresses all background pipelines for that request. `HookResponse(dispatch=False)` can also suppress them explicitly. A submission without a responder, or with an empty response, accepts/closes the view and admits matching pipelines. Once a response accepts a submission, a later pipeline failure cannot reopen validation.

| Callback | Responder body |
| --- | --- |
| Events, controls, Block Kit actions, shortcuts, modal closure, dialog cancellation | `None` or an empty mapping; use consumer-owned API calls for other effects. |
| `block_suggestion` | `options` or `option_groups`, with plain-text composition objects and string values. |
| Legacy options/dialog suggestions | Options/groups with legacy string text. |
| `view_submission` | Empty, or `response_action` of `errors`, `update`, `push`, or `clear`; view changes include a modal view with blocks. |
| `dialog_submission` | Empty, or a nonempty `errors` list with `name` and `error`; errors suppress dispatch. |
| Slash commands and legacy interactive message actions | Empty, or message JSON with text/blocks/attachments and optional response flags. |

Anyo validates these response envelopes, JSON encoding, option/group counts, and a one-MiB response limit. Slack still validates detailed Block Kit elements and field limits. Callback return data does not automatically call a `response_url`, open a modal, or complete a function. Those are explicit consumer operations; the async responder can perform bounded API work where a short-lived trigger must be used immediately. Avoid blocking the loop and set external-call deadlines inside Slack's response budget.

Two matching responders are a configuration conflict: HTTP 409 is returned before either runs and no pipelines are admitted. Make their selectors disjoint. Ordinary pipelines may still fan out to any number of matching registrations. A successful immediate response is cached even if subsequent background admission encounters capacity pressure; redelivery can then reuse that reply without repeating the responder.

## Authentication, acknowledgment, and failure

The provider verifies Slack's `v0` HMAC against original bytes and checks the signed timestamp within 300 seconds of its clock. JSON/form parsing follows verification and rejects ambiguous duplicate keys, invalid encoding, non-JSON numbers, and malformed envelopes. The deprecated verification token is never accepted instead of a signature. Legacy interactions lacking an app ID need `allow_legacy_app_id=True` with an exact legacy interaction type and the corresponding app signing secret; workspace/enterprise filters still apply.

Valid setup requests return the supplied challenge with HTTP 200 and never reach a pipeline. Signed `ssl_check=1` probes also get an empty 200. Unsigned legacy probes fail authentication. Normal accepted, ignored, and duplicate Slack deliveries return empty HTTP 200 unless the responder supplies supported data. GitHub retains its existing accepted-count JSON and 202 behavior.

Slack's provider applies a 2.5-second total receive/response deadline, independently of the generic body-read timeout. Immediate responders have a separate concurrency limit equal to `workers` (default four), so occupied background workers do not block menus. New response work at capacity is rejected without a waiting queue. Responders must cooperate with cancellation; a cancelled handler keeps its slot until it exits and may delay shutdown if it ignores cancellation. Like async pipelines, blocking the event loop prevents timely processing and is a consumer error.

Malformed input returns 400, authentication failure 401, unsupported content type 415, oversized input 413, and the generic body-read timeout 408. Conflicting responders return 409. Responder failure, total deadline expiry, missing required responder, runtime unavailability, or capacity/history exhaustion returns 503. Response validation failure runs no background work. Job admission is atomic across newly eligible hooks, and no successful acknowledgment is sent for dropped work. An immediate responder may already have performed its own side effect before later job admission fails; response caching and consumer idempotency address redelivery, not transactional rollback.

## Delivery guarantees

Events use an app-scoped native `event_id`. Rate-limit notices use app, workspace, and minute. Other callbacks use a digest of canonical payload data, excluding the deprecated token. A native trigger ID or action timestamp distinguishes invocations; otherwise the signed request timestamp is included as a fallback. Retry headers do not create a different delivery. Identical callbacks without invocation metadata in the same signed second are ambiguous; a retry with a changed timestamp may also repeat that work. This is bounded deduplication, not exactly-once execution.

The ordinary pipeline history and immediate-response cache each use `max_records` and `retention_seconds` (defaults 10,000 and one hour). Active entries retain their slots; completed entries expire from completion time. Concurrent duplicates share a responder and completed replies are replayed. Failed/cancelled responders can run again on redelivery. Cancelling a request also cancels its shared active responder, so concurrent duplicate waiters may fail and need redelivery. This responder cache is process-local and resets at restart even when the pipeline queue is durable. Multiple receivers can repeat responder side effects; shared pipeline deduplication does not coordinate immediate responders.

Pipeline exceptions remain isolated. SQLite/Redis retain shared pipeline history across process restarts and allow explicit failed/interrupted retry without repeating successful sibling hooks. A `RetryPolicy` can enable bounded replay; Slack transport retries only concern failed acknowledgment and do not automatically recover a pipeline that fails after a 200 response. Consumers need idempotent effects and the [worker recovery policy](worker-queues.md#admission-execution-and-recovery). Completion order, exactly-once responder effects, and automatic fetching of missed events are not guaranteed.

## Isolated applications and provider extension

Use `Application(providers=[SlackProvider()])` for an isolated registry with compatibility memory storage, or include `GitHubProvider()` as well. `SlackProvider(clock=..., request_timeout=...)` allows controlled tests and a shorter total budget; the timeout must be positive and below three seconds. These constructor options are not `anyo.start` arguments. Normal registration and imports have no network effects and do not require a Slack SDK. The package-root API defaults to SQLite; advanced applications compose durable backends as shown in the [worker guide](worker-queues.md#advanced-application-composition-and-asgi). Responders remain in the receiver, and its 2.5-second total budget includes awaited queue admission.

The shared `Provider.decode` return type now includes `WebhookDelivery` in addition to `HookEvent | None`. Existing adapters returning the old types work unchanged. Code calling an arbitrary provider's `decode` directly should narrow this new result variant before accessing an event. `WebhookDelivery` carries an event, acknowledgment, optional response renderer, and a required-responder flag. `ResponseProvider` adds the total `request_timeout` property and response-aware decoding; it enables responder registration. All these extension types live in `anyo.hooks`, independently of Starlette. The receiver and dispatcher contain no Slack-specific branches.

## Coverage limits and sources

The [inventory](../tasks/slack-hooks/coverage.md) records exact catalog names, scopes, restrictions, and local verification cases. The adapter supports arbitrary authenticated event names and payload fields; it does not make unavailable Slack activity observable. Socket Mode, Audit Logs polling, legacy RTM, retired workflow steps, and every outbound Web API method are outside this implementation. New interaction types can be received with empty acknowledgments; new response schemas may require an adapter update.

Protocol constraints come from the official sources linked in [research](../tasks/slack-hooks/research.md). Engineering decisions are recorded in [the task design](../tasks/slack-hooks/design.md). Verification commands and actual outcomes belong to [the task](../tasks/slack-hooks/plan.md); local fixtures do not establish successful live workspace delivery.

Worker integration is tracked separately in the [SQLite and Redis task](../tasks/sqlite-redis-workers/plan.md), including its completed verification.
