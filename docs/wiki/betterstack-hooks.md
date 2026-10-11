# Connect Better Stack

## For Humans

Start your Python pipeline when Better Stack reports an incident or a new application error. Anyo authenticates the request and queues your function for a worker.

## Run the example

Install Anyo from this checkout and save a random secret outside your code:

```sh
python -m pip install .
export ANYO_BETTERSTACK_WEBHOOK_SECRET='replace-with-a-long-random-secret'
export ANYO_BETTERSTACK_APPLICATION_ID='4821'
python examples/betterstack_webhook.py
```

Replace `4821` with your Better Stack Errors application ID. The [example](../../examples/betterstack_webhook.py) prints an incident/error identifier and delivery ID. Replace its `investigate` callback with your own code; you can [run an AI prompt](run-prompts.md) inside it.

The listener is `127.0.0.1:8000/webhooks/betterstack`. Jobs use `.anyo/betterstack.sqlite3`. To add the integration to another project, the core registration is:

```python
import anyo
from anyo import HookEvent


def investigate(event: HookEvent) -> None:
    print(event.action, event.delivery_id)


if __name__ == "__main__":
    anyo.add_hook(
        provider="betterstack",
        action="incident",
        event_type="*",
        pipeline=investigate,
        hook_id="investigate-incidents",
    )
    anyo.start()
```

Use `action="error", event_type="new_error", application_id="4821"` for new errors. Both synchronous and asynchronous callbacks work. Native context is available by narrowing the event to `BetterStackEvent` from `anyo.integrations.betterstack`.

## Connect the outgoing webhooks

1. Expose the listener through a public HTTPS endpoint such as `https://your-host/webhooks/betterstack`. Preserve the body, content type, and secret header through your reverse proxy.
2. In Better Stack, open **Incidents → Integrations → Exporting data → Outgoing webhooks** and add an incident webhook. Select the incident changes that should start your pipeline, for example creation and reopening. Check that your notification/escalation configuration invokes this webhook for the intended incidents. [Better Stack incident setup](https://betterstack.com/docs/uptime/webhooks/).
3. Configure POST, the default JSON body, `Content-Type: application/json`, and the custom header `X-Anyo-Webhook-Secret` with the same value as `ANYO_BETTERSTACK_WEBHOOK_SECRET`. Better Stack's request template settings support custom headers. [Request settings](https://betterstack.com/docs/uptime/api/create-outgoing-webhook-integration/).
4. To receive errors directly, open **Errors → Outgoing webhooks**, add an Error webhook, select **New error**, and use the same HTTPS URL, POST/JSON settings, and secret header. Use **Send a test webhook** to check reception. [Better Stack error setup](https://betterstack.com/docs/errors/integrations/outgoing-webhooks/).

Choose a printable ASCII secret without whitespace or commas; a long random hexadecimal value works. Anyo uses the custom header, not Basic authentication or an API token. The receiver requires the secret; a separate worker does not.

Incident payloads without a native `event` are delivered with `event_type="unspecified"`. The example's `event_type="*"` accepts them, so select the desired changes in Better Stack. Do not use an exact lifecycle filter until you have confirmed the event name in your integration's payload. Test sends can execute the pipeline just like ordinary notifications.

## Check it locally

While the example is running, send a synthetic incident from another terminal with the same secret exported:

```sh
curl --fail-with-body http://127.0.0.1:8000/webhooks/betterstack \
  -H 'Content-Type: application/json' \
  -H "X-Anyo-Webhook-Secret: $ANYO_BETTERSTACK_WEBHOOK_SECRET" \
  --data '{"data":{"id":"local-check-1","type":"incident","attributes":{"name":"Local check"}}}'
```

The response is an empty `200`. The example prints `incident local-check-1: unspecified` followed by a delivery ID. Repeating identical JSON is suppressed while its successful record is retained. Use a different ID for another check. `200` confirms admission, not completion; inspect pipeline failures through the [dashboard](operations-ui.md) or worker logs. This local fixture does not test Better Stack's delivery configuration.

## Separate workers and choose a queue

The example accepts `ANYO_ROLE=receiver` or `ANYO_ROLE=worker`, `ANYO_QUEUE_URL`, `ANYO_QUEUE_NAMESPACE`, `ANYO_HOST`, `ANYO_PORT`, and `ANYO_EXECUTION_MODE=thread` or `process`. These are example settings, not automatic library environment discovery. Both roles need the same application ID, registrations, queue, and namespace. Process execution requires Linux or macOS and a guarded Python file with top-level callbacks, as in the example.

For Redis, install `python -m pip install '.[redis]'` and set `ANYO_QUEUE_URL` to your Redis endpoint. Follow [separate workers](separate-workers.md) and [queue configuration](workers.md). Changing registrations in an existing namespace requires the documented drained cutover.

Log/metric errors can trigger this integration by configuring a Better Stack alert that creates an incident. If you also turn a tracked error into an incident, direct error and incident hooks are separate triggers; select one route if you want one investigation. Read the [full contract](../knowledge/betterstack-hooks.md) for filters, privacy, and duplicate-delivery limits.

[All guides](README.md)
