# Connect Slack

## For Humans

Run a Python function when Slack sends an event or command. The example handles mentions, reactions, `/anyo`, a button, and a menu.

## Start the example

[Install Anyo](../../README.md#try-it). From the checkout, use your Slack app and workspace values:

```sh
export ANYO_SLACK_APP_ID='A0123456789'
export ANYO_SLACK_TEAM_ID='T0123456789'
export ANYO_SLACK_SIGNING_SECRET='your-app-signing-secret'
python examples/slack_webhook.py
```

The app ID and signing secret are in your Slack app's **Basic Information** settings. The team ID identifies your workspace.

## Connect the app

1. Forward a public HTTPS URL to `http://127.0.0.1:8000`.
2. Use the [example app manifest](../../examples/slack-app-manifest.json), replacing every placeholder URL with `https://your-public-host/webhooks/slack`.
3. Configure the app while the example is running, so Slack can verify the URL.
4. Install the app into your workspace and invite its bot to a test channel.
5. Mention the bot or run `/anyo`. Watch the terminal for the event.

The manifest includes the example's events, commands, and permissions. More events may need extra subscriptions or permissions.

## Change what runs

Edit `pipeline` in [slack_webhook.py](../../examples/slack_webhook.py). It receives a `SlackEvent` with the event type, workspace ID, and payload.

Background work goes in `pipeline`. Immediate replies go in an async `responder`, for example:

```python
from anyo import HookResponse


async def command_response(event):
    return HookResponse({"text": "Anyo accepted your command."})
```

Attach it with `responder=command_response` when registering the command. Keep it fast so Slack gets its reply within the deadline.

More detail: [manual app setup and hook options](../knowledge/slack-hooks.md) and [supported Slack actions](../tasks/slack-hooks/coverage.md).

[All guides](README.md)
