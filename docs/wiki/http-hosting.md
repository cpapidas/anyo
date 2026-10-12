# Use your own HTTP server or extend Anyo's app

## For Humans

Run Anyo inside your existing Python service, or add routes and middleware before starting Anyo's server. Both approaches use the same hooks and workers.

## Serve Anyo with your server

[Install Anyo](../../README.md#try-it), then save this as `main.py`:

```python
import anyo


def review(event: anyo.HookEvent) -> None:
    print(f"Received PR #{event.pull_request_number}")


anyo.add_hook(
    provider="github",
    action="pr_created",
    organization="your-org",
    repository="your-repo",
    pipeline=review,
    hook_id="review",
)
app = anyo.asgi()
```

Replace the repository values, then run an asyncio ASGI server with lifespan enabled. Uvicorn is already included with Anyo:

```sh
export ANYO_GITHUB_WEBHOOK_SECRET='replace-with-a-long-random-secret'
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --lifespan on
```

[Connect GitHub](github-hooks.md#connect-github) to `/webhooks/github`. Anyo receives events and uses its default SQLite queue. `anyo.asgi()` builds the HTTP app; it does not open a listener. The external server owns its event loop and listener.

For independent registries, use `pipelines = anyo.create_application()`, then `pipelines.add_hook(...)` and `pipelines.asgi(...)`. Instances have separate configuration and lifecycle; use separate queue URLs or namespaces for independent durable jobs. The lower-level `anyo.application.Application` remains available for custom provider/backend composition and retains its memory default.

## Add your own routes and middleware

After constructing `app` in `main.py`, add a route:

```python
from starlette.responses import JSONResponse
from starlette.routing import Route


async def health(request):
    return JSONResponse({"status": "ok"})


app.routes.append(Route("/health", health))
```

Add middleware with Starlette's `app.add_middleware(...)` before startup. Preserve webhook request bytes and signature headers, and keep consumer authentication from rejecting provider deliveries. Anyo reserves `/webhooks/{provider}` and, when enabled, `/ui` and its descendants. Routes are matched in order; put broad catch-all routes after Anyo's routes.

To run that exact customized app with Anyo's server, add:

```python
if __name__ == "__main__":
    anyo.serve(app, host="127.0.0.1", port=8000)
```

Then run `python main.py`. Set runtime options such as `workers`, `queue_url`, and `ui` when calling `asgi(...)`; `serve(...)` only takes the app and listener address. The original `anyo.start(...)` remains the quick start for an app without HTTP customization.

## Mount Anyo in an existing application

For a Starlette host, replace `app = anyo.asgi()` with:

```python
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

http = anyo.asgi()


@asynccontextmanager
async def host_lifespan(host):
    async with anyo.lifespan(http) as state:
        yield state


app = Starlette(
    routes=[Mount("/anyo", app=http)],
    lifespan=host_lifespan,
)
```

Add your own host routes alongside the mount. Webhooks are now at `/anyo/webhooks/github`. Mounting alone does not start Anyo: the host must enter `anyo.lifespan(http)` exactly once. Compose this context with your existing host lifespan, and exit it before closing any host resources used by pipelines. Use `AsyncExitStack` to manage several independent Anyo apps; merge yielded state explicitly if your host needs it.

To add resources owned by the Anyo app, pass an async context-manager function as `asgi(lifespan=resources)`. It receives the Starlette app, starts before Anyo's runtime, and exits after workers and harness resources close. It may set `app.state` or yield a mapping for Starlette request state. Hook registration is already frozen during this context; register hooks beforehand.

For the dashboard, pass `ui=True` to `asgi` and set the [UI credentials](operations-ui.md). It opens at `/anyo/ui/`, with assets, requests, and cookies scoped to that prefix. Configure your host's ASGI `root_path` for any additional reverse-proxy prefix, and preserve the external scheme/host for HTTPS and CSRF checks.

The [complete example](../../examples/http_hosting.py) includes a host route, an Anyo route, middleware, custom lifecycle, a mount, and both server choices. See [the example commands](../../examples/README.md#host-and-extend-the-http-application).

This integration requires asyncio ASGI and an enabled lifespan. WSGI and other async backends are not supported adapters. Each HTTP server process owns its own workers, responder cache, and UI sessions; use receiver-only mode plus [separate workers](separate-workers.md) when needed. `workers=` on `asgi` means pipeline concurrency, not HTTP processes.

[Lifecycle and compatibility details](../knowledge/webhook-triggers.md#embedding-and-http-extension) · [All guides](README.md)
