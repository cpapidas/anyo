# Open the dashboard

## For Humans

See pipeline runs and logs in your browser. Use the dashboard to retry failures, cancel work, and control workers.

## Try the example

On Linux or macOS, [install Anyo](../../README.md#try-it), then run from the checkout:

```sh
export ANYO_GITHUB_ORGANIZATION='your-org'
export ANYO_GITHUB_REPOSITORY='your-repo'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
export ANYO_UI_USERNAME='operator'
export ANYO_UI_PASSWORD='replace-with-a-separate-long-random-password'
python examples/operations_ui.py
```

Open [the dashboard](http://127.0.0.1:8000/ui/) and sign in with those UI credentials.

[Connect GitHub](github-hooks.md#connect-github), then open a PR. The example runs for ten seconds and prints progress. To try it without GitHub, send the [local test event](../knowledge/operations-ui.md#start-a-local-dashboard).

## Add it to your application

Set `ANYO_UI_USERNAME` and `ANYO_UI_PASSWORD`. In the [README example](../../README.md#try-it), replace the startup call with:

```python
anyo.start(ui=True, execution_mode="process")
```

Process mode lets you force-stop an individual run. It requires Linux/macOS, an importable top-level pipeline function, and the `if __name__ == "__main__":` guard shown in the example.

Use `execution_mode="thread"` for the default execution mode. A stuck synchronous thread cannot be force-stopped individually.

The dashboard needs SQLite or Redis and is off by default. Use HTTPS when accessing it remotely.

To serve the dashboard inside your own application, use `asgi(ui=True)` and follow [HTTP hosting](http-hosting.md). A mount at `/anyo` puts the dashboard at `/anyo/ui/`; its assets, API calls, and session cookie follow that prefix.

## Open a task's logs

1. Select **Logs** in the sidebar. **Logged tasks** lists runs with retained output, newest activity first.
2. Search by hook name or task ID, then click the task name or **View logs**.
3. Read its output across attempts. Filter by source or severity, pause **Live updates** to inspect output, or use **Load next entries** to continue through longer history.

Expand **Execution details** for timestamps, ownership, and errors. Available run controls remain beside the task. Browser Back/Forward and refresh preserve the selected task; its URL can be bookmarked.

Tasks remain listed while their logs are retained, even if their queue history has expired. These tasks show **logs retained** and have no execution controls. Older logs without a saved hook name show the task ID.

Use **All activity** for the combined stream, including general application logs. Click a task link beneath an entry to open that task's output. A run with no captured output is still accessible from **Runs**.

The panel shows up to 1,000 entries at a time. Pausing affects the view only; workers keep collecting output. Reopen a task to start again from its earliest retained entries. Expired or uncaptured logs cannot be recovered.

## Use separate workers

Use the same hooks, queue URL, and namespace in each process. After registration, the receiver uses:

```python
anyo.start(role="receiver", ui=True)
```

Each worker uses:

```python
anyo.start(role="worker", operations=True, execution_mode="process")
```

Only the receiver needs UI and webhook credentials. The [operations example](../../examples/operations_ui.py) selects these settings from `ANYO_ROLE`.

More detail: [logs and retention limits](../knowledge/operations-ui.md#log-coverage-and-handling), [process restrictions](../knowledge/operations-ui.md#choose-an-execution-mode), and [upgrading an existing queue](../knowledge/operations-ui.md#upgrade-an-existing-queue).

[All guides](README.md)
