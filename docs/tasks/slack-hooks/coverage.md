# Slack callback coverage inventory

## For Humans

Slack's catalog mixes current app events, legacy signals, and subscription names that differ from their received payloads. This inventory accounts for each catalog entry and the command and interaction surfaces implemented by Anyo. It separates local protocol verification from availability in a particular Slack installation.

Status: draft  
Last updated: 2026-10-03  
Scope: 153 event/subtype reference entries retrieved from Slack, plus interactions, commands, control requests, and unavailable surfaces.

## How to read coverage

All Events-compatible entries use the generic signed JSON route; no runtime event allowlist is present. `conditional` means Anyo accepts and routes the native payload, while Slack delivery depends on subscriptions, bot/user scope support, installation visibility, feature enablement, and applicable plan. `unavailable` means the catalog advertises only RTM, which this HTTP provider does not implement. `retired` identifies a documented obsolete feature even if its reference still lists Events compatibility.

Scope cells transcribe the reference's fact panel as identifiers. `none listed` means no additional scope is listed on that page; it never grants access to private data or removes parent message-subscription permissions. Message subtypes use the relevant `message.channels`, `message.groups`, `message.im`, or `message.mpim` subscription and its history scope. Available bot versus user subscriptions must match the actual installation; installing Anyo does not enable them remotely. Admin scopes require eligible administrative installations. Workflows, assistant features, and Work Objects require their respective app features to be configured.

Each name links to the exact Slack source used. Every Events-compatible row is covered by `test_catalog_entry_routes` in [catalog tests](../../../tests/integrations/slack/test_catalog.py), using signed synthetic envelopes with unique IDs and payload preservation assertions. These tests establish generic transport/routing support, not that a live app can subscribe to every row. Special normalization and HTTP response behavior have additional tests in [provider tests](../../../tests/integrations/slack/test_provider.py) and [HTTP tests](../../../tests/integrations/slack/test_application.py). No live workspace was used.

## Events and message subtypes

Unless stated otherwise, use `action="event"`, select the native name with `event_type`, and receive an empty HTTP 200 acknowledgment after admission. `message/foo` below means `event_type="message", subtype="foo"`; the canvas mention exception uses `event_type="app_mention", subtype="document_mention"`. Message subscription aliases also constrain `channel_type`. Generic raw names remain available for future schema additions.

| Slack reference / subscription | API compatibility | Scopes listed | Anyo selector / response | Status |
| --- | --- | --- | --- | --- |
| [accounts_changed](https://docs.slack.dev/reference/events/accounts_changed/) | RTM | none listed | `accounts_changed` | unavailable: RTM only |
| [agent_session_stopped](https://docs.slack.dev/reference/events/agent_session_stopped/) | Events | `chat:write` | `agent_session_stopped` | conditional |
| [agent_session_title_changed](https://docs.slack.dev/reference/events/agent_session_title_changed/) | Events | `chat:write` | `agent_session_title_changed` | conditional |
| [app_context_changed](https://docs.slack.dev/reference/events/app_context_changed/) | Events | `assistant:write` | `app_context_changed` | conditional |
| [app_deleted](https://docs.slack.dev/reference/events/app_deleted/) | Events | `admin.apps:read` | `app_deleted` | conditional |
| [app_home_opened](https://docs.slack.dev/reference/events/app_home_opened/) | Events | none listed | `app_home_opened` | conditional |
| [app_installed](https://docs.slack.dev/reference/events/app_installed/) | Events | none listed | `app_installed` | conditional |
| [app_mention](https://docs.slack.dev/reference/events/app_mention/) | Events | `app_mentions.read` | `app_mention` | conditional |
| [app_rate_limited](https://docs.slack.dev/reference/events/app_rate_limited/) | Events | none listed | `action="control"`, `event_type="app_rate_limited"` | conditional |
| [app_requested](https://docs.slack.dev/reference/events/app_requested/) | Events | none listed | `app_requested` | conditional |
| [app_uninstalled](https://docs.slack.dev/reference/events/app_uninstalled/) | Events | none listed | `app_uninstalled` | conditional |
| [app_uninstalled_team](https://docs.slack.dev/reference/events/app_uninstalled_team/) | Events | `admin.apps:read` | `app_uninstalled_team` | conditional |
| [assistant_thread_context_changed](https://docs.slack.dev/reference/events/assistant_thread_context_changed/) | Events | none listed | `assistant_thread_context_changed` | conditional |
| [assistant_thread_started](https://docs.slack.dev/reference/events/assistant_thread_started/) | Events | none listed | `assistant_thread_started` | conditional |
| [bot_added](https://docs.slack.dev/reference/events/bot_added/) | RTM | none listed | `bot_added` | unavailable: RTM only |
| [bot_changed](https://docs.slack.dev/reference/events/bot_changed/) | RTM | none listed | `bot_changed` | unavailable: RTM only |
| [call_rejected](https://docs.slack.dev/reference/events/call_rejected/) | Events | none listed | `call_rejected` | conditional |
| [channel_archive](https://docs.slack.dev/reference/events/channel_archive/) | Events | none listed | `channel_archive` | conditional |
| [channel_created](https://docs.slack.dev/reference/events/channel_created/) | Events, RTM | `channels:read` | `channel_created` | conditional |
| [channel_deleted](https://docs.slack.dev/reference/events/channel_deleted/) | Events, RTM | `channels:read` | `channel_deleted` | conditional |
| [channel_history_changed](https://docs.slack.dev/reference/events/channel_history_changed/) | Events, RTM | `channels:history`, `groups:history`, `mpim:history` | `channel_history_changed` | conditional |
| [channel_id_changed](https://docs.slack.dev/reference/events/channel_id_changed/) | Events | none listed | `channel_id_changed` | conditional |
| [channel_joined](https://docs.slack.dev/reference/events/channel_joined/) | RTM | none listed | `channel_joined` | unavailable: RTM only |
| [channel_left](https://docs.slack.dev/reference/events/channel_left/) | Events, RTM | `channels:read` | `channel_left` | conditional |
| [channel_marked](https://docs.slack.dev/reference/events/channel_marked/) | RTM | none listed | `channel_marked` | unavailable: RTM only |
| [channel_rename](https://docs.slack.dev/reference/events/channel_rename/) | Events, RTM | `channels:read` | `channel_rename` | conditional |
| [channel_shared](https://docs.slack.dev/reference/events/channel_shared/) | Events | `channels:read`, `groups:read` | `channel_shared` | conditional |
| [channel_unarchive](https://docs.slack.dev/reference/events/channel_unarchive/) | RTM | none listed | `channel_unarchive` | unavailable: RTM only |
| [channel_unshared](https://docs.slack.dev/reference/events/channel_unshared/) | Events | `channels:read`, `groups:read` | `channel_unshared` | conditional |
| [commands_changed](https://docs.slack.dev/reference/events/commands_changed/) | RTM | none listed | `commands_changed` | unavailable: RTM only |
| [dnd_updated](https://docs.slack.dev/reference/events/dnd_updated/) | Events, RTM | `dnd:read` | `dnd_updated` | conditional |
| [dnd_updated_user](https://docs.slack.dev/reference/events/dnd_updated_user/) | Events, RTM | `dnd:read` | `dnd_updated_user` | conditional |
| [email_domain_changed](https://docs.slack.dev/reference/events/email_domain_changed/) | Events, RTM | `team:read` | `email_domain_changed` | conditional |
| [emoji_changed](https://docs.slack.dev/reference/events/emoji_changed/) | Events, RTM | `emoji:read` | `emoji_changed` | conditional |
| [entity_comments_requested](https://docs.slack.dev/reference/events/entity_comments_requested/) | Events | none listed | `entity_comments_requested` | conditional |
| [entity_details_requested](https://docs.slack.dev/reference/events/entity_details_requested/) | Events | none listed | `entity_details_requested` | conditional |
| [external_org_migration_finished](https://docs.slack.dev/reference/events/external_org_migration_finished/) | RTM | none listed | `external_org_migration_finished` | unavailable: RTM only |
| [external_org_migration_started](https://docs.slack.dev/reference/events/external_org_migration_started/) | RTM | none listed | `external_org_migration_started` | unavailable: RTM only |
| [file_change](https://docs.slack.dev/reference/events/file_change/) | Events, RTM | `files:read` | `file_change` | conditional |
| [file_comment_deleted](https://docs.slack.dev/reference/events/file_comment_deleted/) | Events, RTM | `files:read` | `file_comment_deleted` | conditional |
| [file_created](https://docs.slack.dev/reference/events/file_created/) | Events, RTM | `files:read` | `file_created` | conditional |
| [file_deleted](https://docs.slack.dev/reference/events/file_deleted/) | Events, RTM | `files:read` | `file_deleted` | conditional |
| [file_public](https://docs.slack.dev/reference/events/file_public/) | Events, RTM | `files:read` | `file_public` | conditional |
| [file_shared](https://docs.slack.dev/reference/events/file_shared/) | Events, RTM | `files:read` | `file_shared` | conditional |
| [file_unshared](https://docs.slack.dev/reference/events/file_unshared/) | Events, RTM | `files:read` | `file_unshared` | conditional |
| [function_executed](https://docs.slack.dev/reference/events/function_executed/) | Events | none listed | `function_executed` | conditional |
| [goodbye](https://docs.slack.dev/reference/events/goodbye/) | RTM | none listed | `goodbye` | unavailable: RTM only |
| [grid_migration_finished](https://docs.slack.dev/reference/events/grid_migration_finished/) | Events | none listed | `grid_migration_finished` | conditional |
| [grid_migration_started](https://docs.slack.dev/reference/events/grid_migration_started/) | Events | none listed | `grid_migration_started` | conditional |
| [group_archive](https://docs.slack.dev/reference/events/group_archive/) | RTM | none listed | `group_archive` | unavailable: RTM only |
| [group_close](https://docs.slack.dev/reference/events/group_close/) | Events, RTM | `groups:read` | `group_close` | conditional |
| [group_deleted](https://docs.slack.dev/reference/events/group_deleted/) | Events, RTM | `groups:read` | `group_deleted` | conditional |
| [group_history_changed](https://docs.slack.dev/reference/events/group_history_changed/) | Events, RTM | `groups:history` | `group_history_changed` | conditional |
| [group_joined](https://docs.slack.dev/reference/events/group_joined/) | RTM | none listed | `group_joined` | unavailable: RTM only |
| [group_left](https://docs.slack.dev/reference/events/group_left/) | Events, RTM | `groups:read` | `group_left` | conditional |
| [group_marked](https://docs.slack.dev/reference/events/group_marked/) | RTM | none listed | `group_marked` | unavailable: RTM only |
| [group_open](https://docs.slack.dev/reference/events/group_open/) | Events, RTM | `groups:read` | `group_open` | conditional |
| [group_rename](https://docs.slack.dev/reference/events/group_rename/) | Events, RTM | `groups:read` | `group_rename` | conditional |
| [group_unarchive](https://docs.slack.dev/reference/events/group_unarchive/) | RTM | none listed | `group_unarchive` | unavailable: RTM only |
| [hello](https://docs.slack.dev/reference/events/hello/) | RTM | none listed | `hello` | unavailable: RTM only |
| [im_close](https://docs.slack.dev/reference/events/im_close/) | Events, RTM | `im:read` | `im_close` | conditional |
| [im_created](https://docs.slack.dev/reference/events/im_created/) | Events, RTM | `im:read` | `im_created` | conditional |
| [im_history_changed](https://docs.slack.dev/reference/events/im_history_changed/) | Events, RTM | `im:history` | `im_history_changed` | conditional |
| [im_marked](https://docs.slack.dev/reference/events/im_marked/) | RTM | none listed | `im_marked` | unavailable: RTM only |
| [im_open](https://docs.slack.dev/reference/events/im_open/) | Events, RTM | `im:read` | `im_open` | conditional |
| [invite_requested](https://docs.slack.dev/reference/events/invite_requested/) | Events | `admin.invites:read` | `invite_requested` | conditional |
| [link_shared](https://docs.slack.dev/reference/events/link_shared/) | Events | `links:read` | `link_shared` | conditional |
| [manual_presence_change](https://docs.slack.dev/reference/events/manual_presence_change/) | RTM | none listed | `manual_presence_change` | unavailable: RTM only |
| [member_joined_channel](https://docs.slack.dev/reference/events/member_joined_channel/) | Events, RTM | `channels:read`, `groups:read`, `mpim:read` | `member_joined_channel` | conditional |
| [member_left_channel](https://docs.slack.dev/reference/events/member_left_channel/) | Events, RTM | `channels:read`, `groups:read` | `member_left_channel` | conditional |
| [message](https://docs.slack.dev/reference/events/message/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` | conditional |
| [message.app_home](https://docs.slack.dev/reference/events/message.app_home/) | Events | none listed | `message.app_home` | retired workspace-app feature; parsing retained |
| [message.channels](https://docs.slack.dev/reference/events/message.channels/) | Events | `channels:history` | `message.channels` | conditional |
| [message.groups](https://docs.slack.dev/reference/events/message.groups/) | Events | `groups:history` | `message.groups` | conditional |
| [message.im](https://docs.slack.dev/reference/events/message.im/) | Events | `im:history` | `message.im` | conditional |
| [message.mpim](https://docs.slack.dev/reference/events/message.mpim/) | Events | `mpim:history` | `message.mpim` | conditional |
| [message/assistant_app_thread](https://docs.slack.dev/reference/events/message/assistant_app_thread/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `assistant_app_thread` | conditional |
| [message/bot_message](https://docs.slack.dev/reference/events/message/bot_message/) | Events, RTM | none listed | `message` + `bot_message` | conditional |
| [message/channel_archive](https://docs.slack.dev/reference/events/message/channel_archive/) | Events | none listed | `message` + `channel_archive` | conditional |
| [message/channel_convert_to_private](https://docs.slack.dev/reference/events/message/channel_convert_to_private/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `channel_convert_to_private` | conditional |
| [message/channel_convert_to_public](https://docs.slack.dev/reference/events/message/channel_convert_to_public/) | Events, RTM | none listed | `message` + `channel_convert_to_public` | conditional |
| [message/channel_join](https://docs.slack.dev/reference/events/message/channel_join/) | Events, RTM | none listed | `message` + `channel_join` | conditional |
| [message/channel_leave](https://docs.slack.dev/reference/events/message/channel_leave/) | Events, RTM | none listed | `message` + `channel_leave` | conditional |
| [message/channel_name](https://docs.slack.dev/reference/events/message/channel_name/) | Events, RTM | none listed | `message` + `channel_name` | conditional |
| [message/channel_posting_permissions](https://docs.slack.dev/reference/events/message/channel_posting_permissions/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `channel_posting_permissions` | conditional |
| [message/channel_purpose](https://docs.slack.dev/reference/events/message/channel_purpose/) | Events, RTM | none listed | `message` + `channel_purpose` | conditional |
| [message/channel_topic](https://docs.slack.dev/reference/events/message/channel_topic/) | Events, RTM | none listed | `message` + `channel_topic` | conditional |
| [message/channel_unarchive](https://docs.slack.dev/reference/events/message/channel_unarchive/) | RTM | none listed | `message` + `channel_unarchive` | unavailable: RTM only |
| [message/document_mention](https://docs.slack.dev/reference/events/message/document_mention/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `app_mention` + `document_mention` | conditional |
| [message/ekm_access_denied](https://docs.slack.dev/reference/events/message/ekm_access_denied/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `ekm_access_denied` | conditional |
| [message/file_comment](https://docs.slack.dev/reference/events/message/file_comment/) | Events, RTM | none listed | `message` + `file_comment` | conditional |
| [message/file_mention](https://docs.slack.dev/reference/events/message/file_mention/) | Events, RTM | none listed | `message` + `file_mention` | conditional |
| [message/file_share](https://docs.slack.dev/reference/events/message/file_share/) | Events, RTM | none listed | `message` + `file_share` | conditional |
| [message/group_archive](https://docs.slack.dev/reference/events/message/group_archive/) | RTM | none listed | `message` + `group_archive` | unavailable: RTM only |
| [message/group_join](https://docs.slack.dev/reference/events/message/group_join/) | Events, RTM | none listed | `message` + `group_join` | conditional |
| [message/group_leave](https://docs.slack.dev/reference/events/message/group_leave/) | Events, RTM | none listed | `message` + `group_leave` | conditional |
| [message/group_name](https://docs.slack.dev/reference/events/message/group_name/) | Events, RTM | none listed | `message` + `group_name` | conditional |
| [message/group_purpose](https://docs.slack.dev/reference/events/message/group_purpose/) | Events, RTM | none listed | `message` + `group_purpose` | conditional |
| [message/group_topic](https://docs.slack.dev/reference/events/message/group_topic/) | Events, RTM | none listed | `message` + `group_topic` | conditional |
| [message/group_unarchive](https://docs.slack.dev/reference/events/message/group_unarchive/) | RTM | none listed | `message` + `group_unarchive` | unavailable: RTM only |
| [message/me_message](https://docs.slack.dev/reference/events/message/me_message/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `me_message` | conditional |
| [message/message_changed](https://docs.slack.dev/reference/events/message/message_changed/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `message_changed` | conditional |
| [message/message_deleted](https://docs.slack.dev/reference/events/message/message_deleted/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `message_deleted` | conditional |
| [message/message_replied](https://docs.slack.dev/reference/events/message/message_replied/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `message_replied` | conditional |
| [message/pinned_item](https://docs.slack.dev/reference/events/message/pinned_item/) | Events, RTM | none listed | `message` + `pinned_item` | conditional |
| [message/reminder_add](https://docs.slack.dev/reference/events/message/reminder_add/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `reminder_add` | conditional |
| [message/reply_broadcast](https://docs.slack.dev/reference/events/message/reply_broadcast/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `reply_broadcast` | conditional |
| [message/thread_broadcast](https://docs.slack.dev/reference/events/message/thread_broadcast/) | Events, RTM | `channels:history`, `groups:history`, `im:history`, `mpim:history` | `message` + `thread_broadcast` | conditional |
| [message/unpinned_item](https://docs.slack.dev/reference/events/message/unpinned_item/) | Events, RTM | none listed | `message` + `unpinned_item` | conditional |
| [message_metadata_deleted](https://docs.slack.dev/reference/events/message_metadata_deleted/) | Events | `metadata.message:read` | `message_metadata_deleted` | conditional |
| [message_metadata_posted](https://docs.slack.dev/reference/events/message_metadata_posted/) | Events | `metadata.message:read` | `message_metadata_posted` | conditional |
| [message_metadata_updated](https://docs.slack.dev/reference/events/message_metadata_updated/) | Events | `metadata.message:read` | `message_metadata_updated` | conditional |
| [pin_added](https://docs.slack.dev/reference/events/pin_added/) | Events, RTM | `pins:read` | `pin_added` | conditional |
| [pin_removed](https://docs.slack.dev/reference/events/pin_removed/) | Events, RTM | `pins:read` | `pin_removed` | conditional |
| [pref_change](https://docs.slack.dev/reference/events/pref_change/) | RTM | none listed | `pref_change` | unavailable: RTM only |
| [presence_change](https://docs.slack.dev/reference/events/presence_change/) | RTM | none listed | `presence_change` | unavailable: RTM only |
| [presence_query](https://docs.slack.dev/reference/events/presence_query/) | RTM | none listed | `presence_query` | unavailable: RTM only |
| [presence_sub](https://docs.slack.dev/reference/events/presence_sub/) | RTM | none listed | `presence_sub` | unavailable: RTM only |
| [reaction_added](https://docs.slack.dev/reference/events/reaction_added/) | Events, RTM | `reactions:read` | `reaction_added` | conditional |
| [reaction_removed](https://docs.slack.dev/reference/events/reaction_removed/) | Events, RTM | `reactions:read` | `reaction_removed` | conditional |
| [reconnect_url](https://docs.slack.dev/reference/events/reconnect_url/) | RTM | none listed | `reconnect_url` | unavailable: RTM only |
| [shared_channel_invite_accepted](https://docs.slack.dev/reference/events/shared_channel_invite_accepted/) | Events | none listed | `shared_channel_invite_accepted` | conditional |
| [shared_channel_invite_approved](https://docs.slack.dev/reference/events/shared_channel_invite_approved/) | Events | none listed | `shared_channel_invite_approved` | conditional |
| [shared_channel_invite_declined](https://docs.slack.dev/reference/events/shared_channel_invite_declined/) | Events | none listed | `shared_channel_invite_declined` | conditional |
| [shared_channel_invite_received](https://docs.slack.dev/reference/events/shared_channel_invite_received/) | Events, RTM | none listed | `shared_channel_invite_received` | conditional |
| [shared_channel_invite_requested](https://docs.slack.dev/reference/events/shared_channel_invite_requested/) | Events | none listed | `shared_channel_invite_requested` | conditional |
| [star_added](https://docs.slack.dev/reference/events/star_added/) | Events, RTM | `stars:read` | `star_added` | conditional |
| [star_removed](https://docs.slack.dev/reference/events/star_removed/) | Events, RTM | `stars:read` | `star_removed` | conditional |
| [subteam_created](https://docs.slack.dev/reference/events/subteam_created/) | Events, RTM | `usergroups:read` | `subteam_created` | conditional |
| [subteam_members_changed](https://docs.slack.dev/reference/events/subteam_members_changed/) | Events, RTM | `usergroups:read` | `subteam_members_changed` | conditional |
| [subteam_self_added](https://docs.slack.dev/reference/events/subteam_self_added/) | Events, RTM | `usergroups:read` | `subteam_self_added` | conditional |
| [subteam_self_removed](https://docs.slack.dev/reference/events/subteam_self_removed/) | Events, RTM | `usergroups:read` | `subteam_self_removed` | conditional |
| [subteam_updated](https://docs.slack.dev/reference/events/subteam_updated/) | Events, RTM | `usergroups:read` | `subteam_updated` | conditional |
| [team_access_granted](https://docs.slack.dev/reference/events/team_access_granted/) | Events | none listed | `team_access_granted` | conditional |
| [team_access_revoked](https://docs.slack.dev/reference/events/team_access_revoked/) | Events | none listed | `team_access_revoked` | conditional |
| [team_domain_change](https://docs.slack.dev/reference/events/team_domain_change/) | Events, RTM | `team:read` | `team_domain_change` | conditional |
| [team_join](https://docs.slack.dev/reference/events/team_join/) | Events, RTM | `users:read` | `team_join` | conditional |
| [team_migration_started](https://docs.slack.dev/reference/events/team_migration_started/) | RTM | none listed | `team_migration_started` | unavailable: RTM only |
| [team_plan_change](https://docs.slack.dev/reference/events/team_plan_change/) | RTM | none listed | `team_plan_change` | unavailable: RTM only |
| [team_pref_change](https://docs.slack.dev/reference/events/team_pref_change/) | RTM | none listed | `team_pref_change` | unavailable: RTM only |
| [team_profile_change](https://docs.slack.dev/reference/events/team_profile_change/) | RTM | none listed | `team_profile_change` | unavailable: RTM only |
| [team_profile_delete](https://docs.slack.dev/reference/events/team_profile_delete/) | RTM | none listed | `team_profile_delete` | unavailable: RTM only |
| [team_profile_reorder](https://docs.slack.dev/reference/events/team_profile_reorder/) | RTM | none listed | `team_profile_reorder` | unavailable: RTM only |
| [team_rename](https://docs.slack.dev/reference/events/team_rename/) | Events, RTM | `team:read` | `team_rename` | conditional |
| [tokens_revoked](https://docs.slack.dev/reference/events/tokens_revoked/) | Events | none listed | `tokens_revoked` | conditional |
| [url_verification](https://docs.slack.dev/reference/events/url_verification/) | Events | none listed | setup; challenge response; no pipeline | supported |
| [user_change](https://docs.slack.dev/reference/events/user_change/) | Events, RTM | `users:read` | `user_change` | conditional |
| [user_connection](https://docs.slack.dev/reference/events/user_connection/) | Events, RTM | `users:write` | `user_connection` | conditional |
| [user_guest_status_changed](https://docs.slack.dev/reference/events/user_guest_status_changed/) | Events | `users:read` | `user_guest_status_changed` | conditional |
| [user_huddle_changed](https://docs.slack.dev/reference/events/user_huddle_changed/) | Events, RTM | `users:read` | `user_huddle_changed` | conditional |
| [user_profile_changed](https://docs.slack.dev/reference/events/user_profile_changed/) | Events, RTM | `users:read` | `user_profile_changed` | conditional |
| [user_status_changed](https://docs.slack.dev/reference/events/user_status_changed/) | Events, RTM | `users:read` | `user_status_changed` | conditional |
| [user_typing](https://docs.slack.dev/reference/events/user_typing/) | RTM | none listed | `user_typing` | unavailable: RTM only |

The `message.app_home` page describes a defunct workspace-app feature; modern bot apps use `app_home_opened` and `message.im`. File-comment and star-related legacy entries remain conditional on Slack actually delivering them; their presence in the catalog is not evidence that every current saved-item or file UI action emits them. See the linked references and the [platform boundaries](research.md#coverage-boundaries).

## Interactions, commands, and controls

Configure each Request URL and the Options Load URL to `/webhooks/slack`; requests are signed. Modern interactions require the configured app ID and receiving installation. Legacy payloads that omit `api_app_id` require `allow_legacy_app_id=True` on an exact legacy interaction hook; its signing secret then supplies the app binding. This opt-in never relaxes workspace/enterprise matching. All rows below have local HTTP tests.

| Callback | Registration selector | Configuration and availability | Response / verification |
| --- | --- | --- | --- |
| [Block Kit](https://docs.slack.dev/reference/interaction-payloads/block_actions-payload/) | `interaction`, `block_actions` | Enable interactivity; filters support each action/block pair, messages, App Home, modals, and other supplied containers. | Empty 200; `test_multiple_action_matches_schedule_each_hook_once`. |
| [Global shortcut](https://docs.slack.dev/reference/interaction-payloads/shortcuts-interaction-payload/) | `interaction`, `shortcut` | Register app shortcut/callback ID, enable interactivity and required command permissions. | Empty 200; `test_interaction_types_and_empty_acknowledgment`. |
| [Message shortcut](https://docs.slack.dev/reference/interaction-payloads/shortcuts-interaction-payload/) | `interaction`, `message_action` | App-owned shortcut; message context is preserved. | Empty 200; same parameterized interaction test. |
| [Modal submission](https://docs.slack.dev/reference/interaction-payloads/view-interactions-payload/) | `interaction`, `view_submission` | Enable interactivity; match view callback ID. | Empty close, errors, update, push, clear; `test_modal_response_owns_acceptance_and_replays_on_retry`, `test_modal_view_responses`. |
| [Modal closure](https://docs.slack.dev/reference/interaction-payloads/view-interactions-payload/) | `interaction`, `view_closed` | The view must set `notify_on_close`. | Empty 200; parameterized interaction test. |
| [External options](https://docs.slack.dev/reference/interaction-payloads/block_suggestion-payload/) | `interaction`, `block_suggestion` | Configure external select and Options Load URL; matching responder required. | Options/groups JSON; `test_dynamic_menu_response_and_required_responder`. |
| [Slash command](https://docs.slack.dev/interactivity/implementing-slash-commands/) | `slash_command`, `command="/name"` or `*` | Register commands owned by this app; `commands` permission, app installation, command Request URL. | Empty 200 or immediate message JSON; command HTTP tests. |
| [Legacy message action](https://docs.slack.dev/legacy/legacy-messaging/legacy-interactive-message-field-guide/) | `interaction`, `interactive_message`, `options_request=False` | Conditional legacy app feature; action name is exposed as `action_id`. | Empty/message JSON; legacy compatibility tests. |
| [Legacy message options](https://docs.slack.dev/legacy/legacy-messaging/legacy-interactive-message-field-guide/) | `interaction`, `interactive_message`, `options_request=True` | Same discriminator, options request shape; responder required. | Legacy option text/groups; dynamic-menu HTTP tests. |
| [Legacy dialogs](https://docs.slack.dev/legacy/legacy-dialogs/) | `interaction`, `dialog_submission` / `dialog_cancellation` / `dialog_suggestion` | Conditional legacy features, cancellation notifications enabled by the app. | Empty ack, named errors, or options; dialog and menu HTTP tests. |
| [URL verification](https://docs.slack.dev/apis/events-api/using-http-request-urls/) | Automatic setup handling | Configure Events API Request URL with the signing secret already available. | Challenge 200, no pipeline; setup tests. |
| [SSL check](https://docs.slack.dev/interactivity/implementing-slash-commands/) | Automatic probe handling | Only signed `ssl_check=1` probes; token-only legacy probes are rejected. | Empty 200, no pipeline; setup tests. |
| [Rate-limit notice](https://docs.slack.dev/reference/events/app_rate_limited/) | `control`, `app_rate_limited` | Automatic notice; no normal event wrapper/ID. | Empty 200; `test_rate_limited_has_its_own_stable_identity`. |

The subtype/type fields are not a closed enum. Future event and interaction names can be matched; unrecognized interaction types support empty acknowledgments, not invented response schemas. A future callback requiring a new response protocol needs a provider update. [Future-type tests](../../../tests/integrations/slack/test_provider.py) verify payload preservation.

## Other surfaces

- Retired legacy Workflow Builder callbacks: `workflow_step_execute`, `workflow_published`, `workflow_unpublished`, `workflow_deleted`, and `workflow_step_deleted`. Use current `function_executed` for app-owned custom functions. [Retirement source](https://docs.slack.dev/reference/scopes/workflow.steps.execute).
- Socket Mode is an unimplemented alternative transport. Audit Logs actions require an unimplemented polling integration. RTM-only rows above remain unavailable through this provider. [Boundary research](research.md#coverage-boundaries).
- Receiving `function_executed`, link-sharing, or entity-request callbacks does not complete Slack's business protocol. Consumers own function completion, unfurling, and entity response API calls, using preserved payload context and tokens. Immediate responders can perform bounded async API work when required; the receiver itself performs no outbound calls.
- Private/inaccessible activity, other apps' interactions and commands, and Slack UI actions without a documented callback are not observable merely by configuring a catch-all.

## Sources and maintenance

The per-entry facts were retrieved from the official event reference and linked subtype pages on 2026-10-03. The [offline test snapshot](../../../tests/integrations/slack/catalog_snapshot.py) records those same names, API labels, scope identifiers, and source URLs. Reconcile this document and the test snapshot when Slack adds or retires documented surfaces; runtime registration does not need a release just to accept a new event name.
