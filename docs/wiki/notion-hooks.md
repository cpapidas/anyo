# Connect Notion

## For Humans

Start your Python pipeline when Notion sends a page change or another native event. First verify a subscription, then run the receiver with its signing token.

## Collect and confirm the token

Install from this checkout with Python 3.12 or later. On Linux or macOS, create a private directory and start the setup helper:

```sh
python -m pip install .
mkdir -p "$HOME/.config/anyo/notion"
chmod 700 "$HOME/.config/anyo/notion"
python -m anyo.integrations.notion verify \
  --token-file "$HOME/.config/anyo/notion/token"
```

The helper listens at `127.0.0.1:8000/webhooks/notion` for up to ten minutes. Use `--host`, `--port`, or `--timeout` to change those settings. It saves one candidate without printing it or running pipelines. Choose a fresh filename if the destination already exists.

1. Expose that path through a public HTTPS URL, such as `https://your-host/webhooks/notion`, preserving the request body and headers.
2. Open your Notion connection settings, then **Webhooks → Create a subscription**. Enter the HTTPS URL, select `page.content_updated`, and choose API version `2026-03-11` when available.
3. Notion sends a candidate to the helper. Open the saved file securely and paste its value into Notion's **Verify subscription** dialog. A saved candidate is not proof of origin: continue only after Notion accepts it. If it fails, use a fresh destination and Notion's resend flow.
4. After confirmation, stop the helper with Ctrl-C. Keep the same public URL and start the normal receiver below. During the handoff, events must fail or receive a non-success response so they can retry; do not configure a proxy to acknowledge discarded requests.

These are Notion's [subscription and verification steps](https://developers.notion.com/reference/webhooks). Grant the connection access to the page you intend to edit. Comment notifications also require read-comments capability. The normal receiver never accepts unsigned verification requests.

## Run the example

After successful UI confirmation, load the token and supply your workspace and subscription UUIDs from the subscription/setup context. Replace both ID placeholders:

```sh
export ANYO_NOTION_VERIFICATION_TOKEN="$(cat "$HOME/.config/anyo/notion/token")"
export ANYO_NOTION_WORKSPACE_ID='replace-with-workspace-uuid'
export ANYO_NOTION_SUBSCRIPTION_ID='replace-with-subscription-uuid'
python examples/notion_webhook.py
```

Keep shell tracing disabled when loading credentials. A secret manager can supply the variable instead; the REST access token is a different credential. After storing the confirmed token securely, remove the temporary candidate file when it is no longer needed.

Edit a page the connection can access. After Notion delivers the event, the [example](../../examples/notion_webhook.py) prints `Notion page.content_updated: <page UUID>`. It uses `.anyo/notion.sqlite3` and 48-hour terminal history. Notion may combine edits and deliver out of order, so do not expect one immediate run per keystroke.

To use another native event, set `ANYO_NOTION_EVENT_TYPE` and update the subscription's selected events in Notion. Use `*` explicitly to accept all received native types. The example registers a stable hook ID; changing its filters in an existing queue requires the [documented namespace cutover](../knowledge/worker-queues.md#identity-and-namespace-compatibility).

## Use the hook in your own project

```python
import anyo
from anyo import HookEvent
from anyo.integrations.notion import NotionEvent


def page_changed(event: HookEvent) -> None:
    if isinstance(event, NotionEvent):
        print(event.event_type, event.entity_id)


if __name__ == "__main__":
    anyo.add_hook(
        provider="notion",
        action="event",
        event_type="page.content_updated",
        pipeline=page_changed,
        hook_id="notion-page-changed",
    )
    anyo.start(retention_seconds=172800)
```

Optional filters select `workspace_id`, `integration_id`, `subscription_id`, `entity_id`, and `entity_type`. A database entity filter does not include its child pages. Use the existing `secret=` override for multiple subscriptions with different confirmed tokens. Pipelines may be synchronous or asynchronous; immediate `responder=` callbacks are unsupported.

Events carry IDs and notification metadata, not full page content or old/new property values. Replace the callback with your own workflow or [AI prompt](run-prompts.md); fetch content with your own client if needed. Deletion events do not require a successful content fetch. The [full contract](../knowledge/notion-hooks.md) covers validation, native types, identity, and privacy.

## Separate workers and troubleshoot

The example accepts `ANYO_ROLE=receiver` or `worker`, `ANYO_QUEUE_URL`, `ANYO_QUEUE_NAMESPACE`, `ANYO_HOST`, `ANYO_PORT`, and `ANYO_EXECUTION_MODE=thread` or `process`. Both roles need the same registrations and queue settings. Worker-only startup needs no verification token. Follow [separate workers](separate-workers.md) and [SQLite/Redis setup](workers.md); these deployment environment variables belong to the example.

An empty HTTP `200` confirms admission, a retained duplicate, or an unmatched notification; inspect execution through worker logs or the [dashboard](operations-ui.md). `401` means signature/token failure, `400` malformed data, and `503` unavailable capacity, storage, or a deadline. Check the selected upstream events, source access, capabilities, and subscription status if nothing arrives. Setup-helper `503` responses to ordinary events are intentional until you start the normal receiver.

The [task](../tasks/notion-hooks/plan.md#implementation-verification) distinguishes local verification from live Notion checks. No account-specific setup has been performed for you.

[All guides](README.md)
