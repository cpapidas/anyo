# Slack hook coverage research

## For Humans

Slack exposes several ways for an app to receive activity, and each has different permissions and response rules. A comprehensive Anyo integration needs to handle these differences while keeping one understandable registration flow. This research supports the [Slack hooks task](plan.md) and separates available callbacks from actions that cannot be observed through app webhooks.

Status: draft  
Last updated: 2026-10-03  
Scope: official Slack documentation reviewed for inbound trigger coverage and the changes needed in Anyo. This is a planning baseline, not a completed per-event conformance inventory or evidence of live Slack access.

## Coverage matrix

The implementation target is generic support for every supported Events API type and subtype, plus each interaction and command family below. Representative names illustrate coverage; they are not an exhaustive allowlist. The full [Slack event reference](https://docs.slack.dev/reference/events/) distinguishes Events API compatibility from legacy RTM compatibility and must be reconciled entry by entry during implementation.

| Surface | Examples or native discriminator | Coverage requirement and setup |
| --- | --- | --- |
| Messages and threads | `message`, with or without `subtype` | Cover public/private conversations, direct/group messages, replies, edits, deletions, bot messages, and other deliverable subtypes. Preserve thread and message timestamps as strings. Subscription names such as `message.channels` are not the received `event.type`. [Message reference](https://docs.slack.dev/reference/events/message/). |
| Mentions | `app_mention`, including `document_mention` subtype | Cover mentions the app receives, including documented canvas mentions; preserve native type/subtype. Canvas mention support does not establish coverage for every canvas edit. [App mentions](https://docs.slack.dev/reference/events/app_mention/), [canvas mentions](https://docs.slack.dev/reference/events/message/document_mention/). |
| Reactions | Reaction additions and removals | Preserve item context; the channel can be nested under `item`, not at the event root. [Reaction example](https://docs.slack.dev/reference/events/reaction_added/). |
| Conversation lifecycle and membership | Channel/group/DM lifecycle, sharing, rename, archive, member joins/leaves | Support all entries marked Events-compatible, including events without a message body. Record bot/user subscription differences and per-event scopes. |
| Files, pins, and saved items | File lifecycle/sharing; pin and star callbacks where offered | Route deliverable events even if objects are partial. Do not fetch content automatically or infer complete coverage of Slack's current saved-item UI from historical event names. |
| People, groups, and workspace settings | User/profile/status/huddle, DND, user-group and team changes | Preserve event-specific payloads and optional actors. Distinguish supported user status updates from legacy presence-stream events. |
| App and installation lifecycle | Installation/uninstallation, requests, revocation, workspace access, migration | Cover available lifecycle callbacks with their installation/admin restrictions; routing must not assume every event has a channel or user. |
| Slack Connect and links | Shared-channel invitations, sharing changes, link sharing | Account for authorization context across workspaces. Event observation does not automatically approve invitations or create unfurls. |
| Assistant, agent, and Work Object activity | Assistant-thread, agent-session, app-context, and entity-request events in the current catalog | Include newer families through the same generic path; document feature enablement and any callback-specific response obligations. |
| Workflow functions | `function_executed` | Receive app-owned custom function invocations and preserve inputs, execution IDs, and temporary token data privately. Receipt alone does not complete a workflow function. [Function event](https://docs.slack.dev/reference/events/function_executed/). |
| Block Kit interactions | `block_actions` | Support every interactive element that emits this payload across supported surfaces. Match action and block IDs, preserve action arrays and state, and support organization installs where `team` is null. [Payload reference](https://docs.slack.dev/reference/interaction-payloads/block_actions-payload/). |
| Shortcuts | `shortcut`, `message_action` | Cover both global and message shortcuts, with callback IDs and available message context. The dedicated reference and example use singular `message_action`; do not mistake the overview's plural label for the payload discriminator. [Shortcut payloads](https://docs.slack.dev/reference/interaction-payloads/shortcuts-interaction-payload/). |
| Modals | `view_submission`, `view_closed` | Preserve view state and callback IDs. Closing notifications require `notify_on_close`; submission handling must support immediate validation and view responses. [View payloads](https://docs.slack.dev/reference/interaction-payloads/view-interactions-payload/), [modal responses](https://docs.slack.dev/surfaces/modals/). |
| Dynamic external menus | `block_suggestion` | Receive queries through the Options Load URL and return valid options or option groups within the response deadline. [Suggestion payload](https://docs.slack.dev/reference/interaction-payloads/block_suggestion-payload/). |
| App commands | Flat form body containing `command` | Route each configured app-owned slash command; retain text, user/channel context, and response metadata. This does not intercept built-in commands or commands owned by other apps. [Command guide](https://docs.slack.dev/interactivity/implementing-slash-commands/). |
| Legacy app interactions | `interactive_message`, `dialog_submission`, `dialog_cancellation`, legacy option requests | Include compatibility where Slack still delivers these to supported apps; keep legacy status visible. An options request may share a discriminator with an action, so classify by documented request shape or endpoint role. [Message field guide](https://docs.slack.dev/legacy/legacy-messaging/legacy-interactive-message-field-guide/), [dialogs](https://docs.slack.dev/legacy/legacy-dialogs/). |
| Setup and delivery controls | `url_verification`, `app_rate_limited`, command `ssl_check` | Respond to setup/probes without business dispatch; expose actionable delivery notices under a documented policy. These are not uniformly wrapped event callbacks. [HTTP setup](https://docs.slack.dev/apis/events-api/using-http-request-urls/), [rate-limit envelope](https://docs.slack.dev/reference/events/app_rate_limited/), [commands](https://docs.slack.dev/interactivity/implementing-slash-commands/). |

Rows without an individual event link describe families in the [event catalog](https://docs.slack.dev/reference/events/), not a promise that every Slack UI operation has a corresponding callback. The implementation inventory must resolve exact names, scopes, and compatibility using each event's own reference page.

## Subscription and installation constraints

Slack sends subscribed events visible to the installation's authorized user or bot. Access to a private channel elsewhere in the workspace is not implied by app installation. Some events support user subscriptions but not bot subscriptions, and events can include only partial object data. Native event names and raw payload access should therefore remain available without requiring a uniform channel/user schema. [Events API permission and delivery model](https://docs.slack.dev/apis/events-api/).

Examples for consumer setup are `app_mentions:read` for app mentions, `channels:history` for public-channel messages, `im:history` for direct messages, and `reactions:read` for reaction additions. The inventory must record the actual scopes for every chosen event rather than extrapolate these examples to other families. [Mention requirements](https://docs.slack.dev/reference/events/app_mention/), [channel-message requirements](https://docs.slack.dev/reference/events/message.channels/), [DM requirements](https://docs.slack.dev/reference/events/message.im/), [reaction requirements](https://docs.slack.dev/reference/events/reaction_added/).

Anyo registration and Slack subscription are separate operations. The implementation keeps app creation, installation, permissions, and subscription configuration consumer-owned, matching the manual GitHub setup model. A local catch-all does not grant access or request all Slack scopes. Installation changes and additional scopes may require reinstalling the app; the setup guide must describe this without building an OAuth service into the hook receiver.

## Protocol implications

Slack sends event callbacks as JSON, while interactions wrap JSON inside a URL-encoded `payload`. Slash commands use ordinary form fields. Signing verification must use the untouched request bytes and signed timestamp before these parsing steps. The plan proposes rejecting requests more than five minutes away from the receiver's clock, following Slack's verification example. [Request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/), [interaction encoding](https://docs.slack.dev/interactivity/handling-user-interaction/), [command encoding](https://docs.slack.dev/interactivity/implementing-slash-commands/).

Ordinary notifications can enter Anyo's background dispatcher, but interactive responses need an additional path. A menu query requires option data; a rejected modal submission needs a response containing validation errors. Modal response actions can update, push, or clear views, while an empty success response closes the submitted view. Those choices must finish within Slack's response window. [Menu responses](https://docs.slack.dev/reference/interaction-payloads/block_suggestion-payload/), [modal behavior](https://docs.slack.dev/surfaces/modals/).

Short-lived `trigger_id` values cannot reliably wait behind queued pipelines before opening a modal. Some payloads also contain a `response_url`, while others do not. The design needs an explicit bounded responder and consumer guidance for immediate API work; it must not treat background callback return values as an HTTP response. [Interaction response lifecycle](https://docs.slack.dev/interactivity/handling-user-interaction/).

Workflow functions need a further distinction: acknowledging `function_executed` only confirms receipt. The consumer must eventually complete or fail the function using the appropriate API and execution ID; Anyo should preserve the necessary context and document ownership rather than report workflow success on acknowledgment. [Function payload](https://docs.slack.dev/reference/events/function_executed/), [successful completion](https://docs.slack.dev/reference/methods/functions.completeSuccess/).

Events include a native event ID, but that schema cannot be imposed on all interaction or command payloads. [Event envelope](https://docs.slack.dev/reference/objects/event-object/). Slack retries failed event delivery and supplies retry metadata; callback failure after acknowledgment is a separate Anyo concern. The implementation must define identity by family and retain the existing bounded, process-local delivery guarantees. [Retry behavior](https://docs.slack.dev/apis/events-api/).

## Coverage boundaries

| Surface | Finding and proposed task boundary |
| --- | --- |
| Socket Mode | Delivers Events API and interactive payloads over an authenticated WebSocket instead of public request URLs. It is an alternative transport, not permission to receive every workspace action. Keep the initial task on HTTP; a Socket Mode adapter would require separate connection, token, reconnect, and acknowledgment lifecycle work. [Socket Mode guide](https://docs.slack.dev/apis/events-api/using-socket-mode/). |
| Audit-only administrative/security activity | The Audit Logs API uses authenticated GET requests and is restricted to Enterprise organizations with the required owner installation and `auditlogs:read` scope. Covering audit-only actions would need polling, checkpoints, pagination, and a separate delivery contract. Record this as a distinct extension, not completed webhook coverage. [Audit guide](https://docs.slack.dev/admins/audit-logs-api/), [methods and actions](https://docs.slack.dev/reference/audit-logs-api/methods-actions-reference/). |
| RTM-only activity | The legacy RTM API is unavailable to granular-permission apps. Typing indicators and some presence behavior cannot be assumed available through Events API or Socket Mode. Classify these explicitly and avoid building new support on legacy RTM. [RTM restrictions](https://docs.slack.dev/legacy/legacy-rtm-api/), [migration differences](https://docs.slack.dev/legacy/legacy-app-migration/migrating-classic-apps/). |
| Retired workflow steps | Legacy Steps from Apps and associated events were retired on September 26, 2024. Classify `workflow_step_execute` and related retired events as unavailable; use current custom-function callbacks for supported workflow integration. [Retirement announcement](https://docs.slack.dev/reference/scopes/workflow.steps.execute). |
| Incoming webhooks and outbound API methods | Slack's incoming webhooks send messages into Slack. Posting messages, changing channels, or managing users are outbound operations, not a universal subscription to those actions. Consumers may perform these operations in their pipelines using their own authorized clients; a general outbound Slack SDK is outside this trigger task. [Incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/). |
| Unexposed or inaccessible activity | Do not infer a callback from the existence of a Slack feature or Web API method. For canvas/list/bookmark edits, arbitrary other-app button clicks, built-in commands, or inaccessible private conversations, require an explicit supported event/interaction source before claiming coverage. This is the planning interpretation of Slack's documented per-feature and per-installation delivery model, not a claim that Anyo can observe all UI activity. |

## Implications for the existing Anyo design

These are conclusions from the repository inspection, not Slack API requirements:

- [Provider decoding](../../../src/anyo/hooks.py) returns `HookEvent | None` and cannot currently express a setup challenge or custom response body. Introduce a generic response contract while retaining compatibility with existing providers.
- [HookEvent](../../../src/anyo/hooks.py) requires repository-oriented strings. Plan explicit Slack context without giving those fields a second meaning or breaking existing constructors and callbacks.
- [The HTTP receiver](../../../src/anyo/application.py) checks exact action equality before provider matching and always builds accepted-count JSON. Broad matching and Slack response handling must account for both constraints.
- [The dispatcher](../../../src/anyo/execution/__init__.py) deduplicates by hook ID and delivery ID and deep-copies payloads. New identity rules, context fields, and response replay must cooperate with that behavior. The link follows its later promotion to a package.
- The existing bounded background runtime is useful for ordinary Slack hooks. It cannot by itself guarantee prompt interactive response data when every worker is occupied.

The [task plan](plan.md) turns these findings into scoped implementation work and verifiable completion criteria.

## Sources

Official Slack pages linked at each finding were reviewed on 2026-10-03. No Slack workspace was connected and no event delivery was performed. Sources establish platform behavior; the initial code inspection established the gaps this task needed to address. The delivered selectors, context types, response ownership, and delivery identities are now recorded in [implementation decisions](design.md) and the [consumer guide](../../knowledge/slack-hooks.md).
