# Anyo examples

## For Humans

A pipeline needs both an event and the files it will work on. These examples fetch repositories into cached workspaces, run jobs from GitHub, Slack, Better Stack, or Notion, and expose the protected operations UI. The worker examples support SQLite or Redis and separate receiver/worker processes.

## Repository workspaces

[Pull projects into cached workspaces](repository-workspaces/README.md) covers the runnable examples, private repository credentials, and separate workers:

- [One project](repository_workspace.py): refresh a branch or check out an exact commit.
- [Several projects](repository-workspaces/pull_projects.py): keep multiple workspaces alive for cross-project work.
- [GitHub worker](repository-workspaces/worker.py): fetch the event's exact PR commit inside a durable worker.

Each run gets its own workspace and keeps downloaded Git data for the next worker. See also the [short API guide](../docs/wiki/repository-workspaces.md) and [persistent Docker volume example](../docs/wiki/repository-deployment.md).

## SQLite and Redis workers

[queued_webhook.py](queued_webhook.py) registers a stable `hook_id` and prints a PR summary without external calls. Set the GitHub variables described below, then run `python examples/queued_webhook.py`. It defaults to a local SQLite file and starts both reception and workers. Set `ANYO_QUEUE_URL`, `ANYO_QUEUE_NAMESPACE`, `ANYO_ROLE` (`all`, `receiver`, or `worker`), and optional `ANYO_HOST` in consumer configuration to select storage and topology.

For Redis, install `python -m pip install '.[redis]'`, supply a running standalone Redis 6.2-or-later service, and set `ANYO_QUEUE_URL=redis://localhost:6379/0`. Start the same example in two terminals with `ANYO_ROLE=receiver` and `ANYO_ROLE=worker` to separate execution. Both roles need the same registry, namespace, and backend; worker-only needs no webhook signing secret. SQLite files must be on the same host, while Redis can serve workers across machines.

The [worker guide](../docs/knowledge/worker-queues.md) covers installation, custom event codecs, explicit retry and inspection, persistent Docker storage, and drained backend cutover. Changing the URL does not transfer existing jobs or delivery history. These environment variables belong to the example, not automatic library discovery.

## Protected operations UI

[operations_ui.py](operations_ui.py) runs a protected dashboard and a ten-second local pipeline that emits Python logging and stdout progress. It makes no model calls. On Linux or macOS, install the package from the repository root and supply credentials through the environment:

```sh
python -m pip install .
export ANYO_GITHUB_ORGANIZATION='demo-owner'
export ANYO_GITHUB_REPOSITORY='demo-repository'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-webhook-secret'
export ANYO_UI_USERNAME='operator'
export ANYO_UI_PASSWORD='replace-with-a-separate-long-random-password'
python examples/operations_ui.py
```

Open `http://127.0.0.1:8000/ui/` and sign in. The example uses `.anyo/operations.sqlite3`, namespace `operations-demo`, and `execution_mode="process"`, which supports targeted Force stop. Its top-level callback and guarded `main()` are required for spawned execution. Use `ANYO_EXECUTION_MODE=thread` for the existing thread model; synchronous thread work cannot be forcibly stopped independently.

The [operations guide](../docs/knowledge/operations-ui.md#start-a-local-dashboard) includes a signed local test delivery, so no GitHub setup or credentials are needed beyond the chosen local webhook secret. To receive real GitHub events, replace the demo repository values and follow [Connect GitHub](#connect-github). The webhook retains signature authentication independently of the UI login.

Set `ANYO_ROLE=receiver` and `ANYO_ROLE=worker` in separate terminals to split execution. Both need the same queue URL, namespace, and registrations; only the receiver needs UI/webhook credentials. The example enables operations on every role and the UI on receiving roles. `ANYO_QUEUE_URL`, `ANYO_QUEUE_NAMESPACE`, `ANYO_HOST`, and `ANYO_PORT` select its deployment settings. See the [operations guide](../docs/knowledge/operations-ui.md) for HTTPS, retention, log coverage, cancellation, and upgrading existing queues.

## Slack example

[slack_webhook.py](slack_webhook.py) handles mentions, reactions, `/anyo`, a button, and external-menu option loads. The [Slack setup guide](../docs/knowledge/slack-hooks.md) explains its environment variables, signing secret, request URLs, scopes, installation, and response handlers. Start from [slack-app-manifest.json](slack-app-manifest.json) after replacing its placeholder HTTPS URLs. It requests only the example's three bot scopes; broader hooks need the subscriptions and permissions in the [coverage inventory](../docs/tasks/slack-hooks/coverage.md).

## Better Stack example

[betterstack_webhook.py](betterstack_webhook.py) starts a local callback for incidents and new errors. [Connect Better Stack](../docs/wiki/betterstack-hooks.md) covers the shared-secret header, outgoing webhook setup, application filtering, a local test delivery, and separate SQLite/Redis workers. No Better Stack API token or model call is required. Some incident templates omit event names, so select desired incident changes in Better Stack; the example accepts every notification from that integration.

## Notion example

[notion_webhook.py](notion_webhook.py) receives native events and prints their type and entity ID. [Connect Notion](../docs/wiki/notion-hooks.md) explains the separate verification-token collector, manual confirmation, signature validation, source filters, and SQLite/Redis workers. Only use a confirmed verification token with the normal receiver; no Notion SDK or REST access token is needed.

## Run the GitHub example

With Python 3.12 or later, install Anyo from the repository root and configure your target repository:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
export ANYO_GITHUB_ORGANIZATION='your-org'
export ANYO_GITHUB_REPOSITORY='your-repo'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
python examples/github_webhook.py
```

Replace all three environment values. Use the repository name alone, without its owner, for `ANYO_GITHUB_REPOSITORY`. `organization` can name an organization or a personal account. The first two variables belong to this example; `ANYO_GITHUB_WEBHOOK_SECRET` is Anyo's default secret setting. [github_webhook.py](github_webhook.py) logs the PR number and head commit; replace `review_pull_request` with your own code.

The server runs until it is stopped and listens on `127.0.0.1:8000`. Importing the library or registering a hook does not open sockets or start workers. Anyo does not check out the PR or call GitHub's API for you.

## Connect GitHub

1. Give GitHub a publicly reachable HTTPS URL forwarding to `http://127.0.0.1:8000`. Use a forwarding tool for local development or an HTTPS reverse proxy when deployed. Preserve the request body and webhook headers.
2. In the target repository's **Settings → Webhooks**, add a webhook with the payload URL `https://your-public-host/webhooks/github` and content type **application/json**.
3. Set its **Secret** to the same value as `ANYO_GITHUB_WEBHOOK_SECRET`. Keep this value outside source control. Leave SSL verification enabled.
4. Choose individual events, select **Pull requests**, and leave the webhook active.
5. Open a new pull request against that repository. GitHub sends `pull_request` with action `opened`; Anyo maps it to `pr_created` and runs the callback. The initial setup ping and other pull request actions are acknowledged without running it.

These steps follow GitHub's [webhook creation guidance](https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks). You need access to configure the repository webhook, but Anyo needs no API token to receive it. Pipeline code that calls GitHub APIs supplies its own credentials. `anyo.start()` does not register the webhook or create the public HTTPS URL.

## Register more than one hook

Call `anyo.add_hook(...)` for each binding before `anyo.start()`. Distinct callbacks may use the same provider, action, and repository; all matching callbacks run independently. An exact duplicate of a callable, configuration, and secret fails at registration. Invalid provider names, unsupported actions, unknown configuration, and callbacks that cannot accept one event also fail early.

Pass a per-hook secret when repositories use different credentials:

```python
import os

import anyo
from anyo import HookEvent


async def pipeline(event: HookEvent) -> None:
    # Await asynchronous work here; do not block the event loop.
    print(event.pull_request_number, event.head_sha)


anyo.add_hook(
    provider="github",
    action="pr_created",
    organization="your-org",
    repository="another-repo",
    pipeline=pipeline,
    secret=os.environ["ANOTHER_REPOSITORY_WEBHOOK_SECRET"],
)
```

Without `secret=...`, Anyo resolves `ANYO_GITHUB_WEBHOOK_SECRET` at startup. Each hook's own secret must validate a delivery before that hook can run. Anyo excludes secrets from hook representations and default logs.

Every callback receives a `HookEvent` containing `provider`, `action`, `delivery_id`, `organization`, `repository`, `pull_request_number`, `head_sha`, and `payload`. The payload is a separate copy per invocation. In the default thread mode, a plain `def` runs in a worker thread, while `async def` runs on the worker's asyncio loop. Process mode runs the callback in its own spawned process. Return values are discarded. Exceptions mark that hook's delivery failed; default logs omit the exception message and payload. Opt-in operations capture also records consumer logging and output under the [redaction and coverage policy](../docs/knowledge/operations-ui.md#log-coverage-and-handling).

## Configure the runtime

All arguments are optional keyword arguments to `anyo.start()` or `Application.start()`:

| Option | Default | Meaning |
| --- | --- | --- |
| `host` | `"127.0.0.1"` | Interface to bind; use `"0.0.0.0"` when your deployment requires it. |
| `port` | `8000` | Listening port; `0` asks the OS to allocate one. |
| `workers` | `4` | Concurrent pipelines in this process. |
| `capacity` | `100` | Running and queued jobs across the durable namespace. |
| `retention_seconds` | `3600` | How long completed delivery records remain. |
| `max_records` | `10000` | Maximum delivery records, including active work. |
| `max_body_bytes` | `1048576` | Maximum accepted request-body size. |
| `request_timeout` | `5` | Deadline in seconds for reading the request body. |
| `queue_url` | `"sqlite:///.anyo/queue.sqlite3"` for `anyo.start()` | Storage connection; advanced `Application` keeps `memory://` unless configured. |
| `queue_namespace` | `"default"` | Application registry and delivery isolation within storage. |
| `role` | `"all"` | Combined receiver/workers, `receiver`, or `worker`. |
| `lease_seconds` | `30` | Running-job lease; renewals occur every third of the interval. |
| `poll_interval` | `0.1` | Idle queue polling delay in seconds. |
| `operation_timeout` | `2` | Storage-operation timeout in seconds. |
| `ui` | `False` | Serve the protected dashboard at `/ui/`; enables operations and requires UI credentials. |
| `operations` | `False` | Enable controls/log collection; accepts `True` or `OperationsSettings`. Requires SQLite or Redis. |
| `execution_mode` | `"thread"` | Use the existing thread model or `"process"` for isolated, individually stoppable attempts on POSIX. |

For example, `anyo.start(host="0.0.0.0", port=8080, workers=8, capacity=200)` changes the listener and pipeline concurrency. `workers` does not start additional server processes. Routes use the fixed path `/webhooks/{provider}`.

An empty application cannot start. Starting inside a running event loop is also an error; use the ASGI application below. Registration stays frozen while running. Durable shutdown stops admission/claims, finishes owned active attempts, and leaves unclaimed backlog for another worker. Memory mode retains its full drain of accepted jobs. Supply deadlines in your callbacks for HTTP requests and other external work; Anyo cannot safely kill an arbitrary synchronous thread. The [operations UI](../docs/knowledge/operations-ui.md#choose-an-execution-mode) adds explicit force stop through process isolation. An uncontrolled process termination leaves durable running attempts for lease recovery and can repeat external effects if replay is enabled. Memory mode can lose accepted work.

## Isolated applications and ASGI hosting

The module-level API uses a default application with GitHub, Slack, Better Stack, and Notion configured. Use an isolated `Application` to own a separate registry or supply provider adapters explicitly:

```python
from anyo import HookEvent
from anyo.application import Application
from anyo.integrations.github import GitHubProvider


def pipeline(event: HookEvent) -> None:
    print(event.delivery_id)


application = Application(providers=[GitHubProvider()])
application.add_hook(
    provider="github",
    action="pr_created",
    organization="your-org",
    repository="your-repo",
    pipeline=pipeline,
)
app = application.asgi(workers=4, capacity=100)
```

Place this code in your own `main.py` and serve it with `python -m uvicorn main:app --lifespan on`. Set the webhook secret in that process's environment. `Application.asgi()` accepts execution, queue, and request settings with role `all` or `receiver`; the ASGI host controls its address, port, and lifecycle. Always run the application's lifespan and use an asyncio backend. This constructor example uses the compatibility memory backend, with process-local delivery history. For shared durable history, supply `queue_backends` and `queue_url` as shown in the [worker guide](../docs/knowledge/worker-queues.md#advanced-application-composition-and-asgi). Every receiver/worker must load the same compatible registrations.

To add future trigger providers, implement the `Provider` contract in `anyo.hooks` and supply the adapter to `Application`. Repository providers each live in `src/anyo/integrations/<provider>/`, exporting their adapter through `__init__.py`. GitHub's folder owns its configuration validation, authentication, and payload mapping; its public import remains `anyo.integrations.github`. See the [provider package guide](../docs/knowledge/webhook-triggers.md#provider-packages-and-extension-contract) for the layout and extension responsibilities.

## Delivery responses and retries

GitHub's delivery page shows the receiver's response:

| Response | Meaning |
| --- | --- |
| `202` | New jobs were accepted; the response includes their count. This does not mean they completed. |
| `200` | Authenticated ping, unsupported event, unmatched repository, or duplicate delivery; no new jobs. |
| `400` | Malformed webhook headers or payload. |
| `401` | Missing or invalid signature. Check the configured secret. |
| `408` / `413` | Body-read deadline or request-size limit exceeded. |
| `503` | Runtime unavailable, insufficient job capacity, or delivery-history capacity reached. |
| `404` / `405` | Unknown provider path or HTTP method other than POST. |

Admission is all-or-nothing for the newly eligible hooks in a delivery. Queued, running, and successful records suppress repeated delivery IDs for each hook. Failed records allow an explicit redelivery to rerun only the failed hooks. Automatic pipeline retry requires an explicit `RetryPolicy`; Anyo does not fetch missed deliveries. Arrange redelivery when needed, using GitHub's [redelivery guidance](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks).

Durable history is shared across a queue namespace and survives process restart; memory mode remains local and resets. Terminal records expire after the retention interval; active records never expire for retention. Failed/interrupted redelivery uses the original stored event while its record is retained. Repetition after expiry, backend cutover, or crash recovery can repeat external effects, so make them idempotent. See the [retry and uncertainty contract](../docs/knowledge/worker-queues.md#admission-execution-and-recovery).

## OpenCode review pipeline

[opencode_review.py](opencode_review.py) configures the harness in Python and calls `anyo.inject_prompt` from a GitHub hook. Its [prompt file](review_pr.md) requests a comments object; the application prints results and progress and raises on failure. Supply the prepared PR diff/workspace and runtime credentials described in the [OpenCode guide](../docs/knowledge/opencode-prompts.md#docker-deployment). Build the root [Dockerfile](../Dockerfile) to run Anyo and OpenCode together without Docker-in-Docker. Async pipelines use `await anyo.ainject_prompt(...)` with an async progress callback.

## Choose OpenCode or native OmO

The review example has two runtime files: [main.py](harness-review/main.py) selects a harness, sends the prompt, and prints the result; [review.md](harness-review/review.md) contains the function to review. Both harnesses use MiniMax M2.7. The [example README](harness-review/README.md) covers API-key setup and running either harness.

With Docker running and `MINIMAX_API_KEY` exported in your shell, run from the repository root:

```sh
docker build -t anyo:local .
docker run --rm -e MINIMAX_API_KEY -e HARNESS=omo anyo:local
```

To choose OpenCode, run the same image with a different environment setting:

```sh
docker run --rm -e MINIMAX_API_KEY -e HARNESS=opencode anyo:local
```

The image installs both executables and includes both example files. No setup script, file mount, or local Python installation is needed. `HARNESS` defaults to `omo`; the API key is supplied only at runtime.

The printed result identifies `ZeroDivisionError` and recommends returning zero for an empty list. Edit the prompt and rebuild the image to try another review. Live Docker checks are opt-in: after building, run `ANYO_LIVE_HARNESSES=1 python -m pytest -m live tests/integrations/omo/test_live.py -q` in a development environment. Those tests use the real MiniMax service.
