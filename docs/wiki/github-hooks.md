# Connect GitHub

## For Humans

Run a Python function when someone opens a pull request. Start with the [small README example](../../README.md#try-it), then connect your repository below.

## Connect GitHub

1. Start `main.py` with `ANYO_GITHUB_WEBHOOK_SECRET` set.
2. Forward a public HTTPS URL to `http://127.0.0.1:8000` using a tunnel or reverse proxy.
3. In your repository's **Settings → Webhooks**, add a webhook:

   | Setting | Value |
   | --- | --- |
   | Payload URL | `https://your-public-host/webhooks/github` |
   | Content type | `application/json` |
   | Secret | The same value as `ANYO_GITHUB_WEBHOOK_SECRET` |
   | Events | **Pull requests** |
   | Active / SSL verification | Enabled |

4. Open a new pull request. The terminal prints its number.

Use your own public hostname. The tunnel or proxy must preserve the request body and headers.

`organization` is the repository owner, including personal accounts. `repository` is only the repository name.

Anyo handles newly opened PRs, including PRs from forks. A setup ping or another PR action does not run this hook. Receiving webhooks needs no GitHub API token.

## Change what runs

Replace the function body in `main.py`:

```python
def pipeline(event: HookEvent) -> None:
    print(event.pull_request_number)
    print(event.head_sha)
```

The original event data is in `event.payload`. Call [RepositoryCache](repository-workspaces.md) when your job needs repository files.

For several hooks, call `anyo.add_hook(...)` once per hook before `anyo.start()`. Use a different `hook_id` for each binding.

An `async def` function works too. Await its network calls so it does not block other work.

## Run the existing example

After [installing Anyo](../../README.md#try-it), run from the checkout:

```sh
export ANYO_GITHUB_ORGANIZATION='your-org'
export ANYO_GITHUB_REPOSITORY='your-repo'
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
python examples/github_webhook.py
```

The owner and repository variables are read by this example; your own application passes them to `add_hook`.

More detail: [multiple hooks and per-hook secrets](../../examples/README.md#register-more-than-one-hook), [runtime options](../../examples/README.md#configure-the-runtime), and the [webhook contract](../knowledge/webhook-triggers.md).

[All guides](README.md)
