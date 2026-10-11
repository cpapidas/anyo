"""Run Slack notification, command, button, and dynamic-menu hooks."""

import os

import anyo
from anyo import HookEvent, HookResponse
from anyo.integrations.slack import SlackEvent


def pipeline(event: HookEvent) -> None:
    """Replace this with consumer-owned work; payloads may contain secrets."""
    assert isinstance(event, SlackEvent)
    print(f"Received {event.event_type} in workspace {event.team_id}")


async def command_response(event: HookEvent) -> HookResponse:
    """Return a button the user can click to exercise block_actions."""
    return HookResponse(
        {
            "response_type": "ephemeral",
            "text": "Anyo accepted your command.",
            "blocks": [
                {
                    "type": "actions",
                    "block_id": "anyo-example",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "approve",
                            "text": {"type": "plain_text", "text": "Run pipeline"},
                            "value": "approve",
                        },
                        {
                            "type": "external_select",
                            "action_id": "project",
                            "placeholder": {"type": "plain_text", "text": "Choose a project"},
                            "min_query_length": 0,
                        },
                    ],
                }
            ],
        }
    )


async def menu_options(event: HookEvent) -> HookResponse:
    """Use action_id=project in an external select to exercise this handler."""
    query = event.payload.get("value", "")
    names = ("Anyo", "Example project")
    options: list[object] = [
        {"text": {"type": "plain_text", "text": name}, "value": name.lower()}
        for name in names
        if isinstance(query, str) and query.casefold() in name.casefold()
    ]
    return HookResponse({"options": options}, dispatch=False)


if __name__ == "__main__":
    # These two variables belong to the example, not to the library API.
    app_id = os.environ["ANYO_SLACK_APP_ID"]
    team_id = os.environ["ANYO_SLACK_TEAM_ID"]
    for native_event in ("app_mention", "reaction_added"):
        anyo.add_hook(
            provider="slack",
            app_id=app_id,
            team_id=team_id,
            action="event",
            event_type=native_event,
            pipeline=pipeline,
        )
    anyo.add_hook(
        provider="slack",
        app_id=app_id,
        team_id=team_id,
        action="slash_command",
        command="/anyo",
        pipeline=pipeline,
        responder=command_response,
    )
    anyo.add_hook(
        provider="slack",
        app_id=app_id,
        team_id=team_id,
        action="interaction",
        interaction_type="block_actions",
        action_id="approve",
        pipeline=pipeline,
    )
    anyo.add_hook(
        provider="slack",
        app_id=app_id,
        team_id=team_id,
        action="interaction",
        interaction_type="block_suggestion",
        action_id="project",
        responder=menu_options,
    )
    anyo.start()
