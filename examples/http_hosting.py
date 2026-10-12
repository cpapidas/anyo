"""Serve this app with your ASGI server, or run this file for Anyo's server."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

import anyo

logger = logging.getLogger(__name__)
pipelines = anyo.create_application()


def review(event: anyo.HookEvent) -> None:
    print(f"Received PR #{event.pull_request_number}", flush=True)


pipelines.add_hook(
    provider="github",
    action="pr_created",
    organization=os.environ.get("ANYO_GITHUB_ORGANIZATION", "demo-owner"),
    repository=os.environ.get("ANYO_GITHUB_REPOSITORY", "demo-repository"),
    pipeline=review,
    hook_id="hosted-review",
)


@asynccontextmanager
async def resources(http: Starlette) -> AsyncIterator[None]:
    # Open any consumer-owned clients here; pipelines may borrow them. Anyo
    # drains its workers before this context exits and those clients close.
    http.state.service_name = "hosted-review"
    logger.info("Consumer resources ready")
    try:
        yield
    finally:
        logger.info("Anyo drained; closing consumer resources")
        del http.state.service_name


async def service(request: Request) -> JSONResponse:
    return JSONResponse({"service": request.app.state.service_name})


class ResponseHeader(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["x-example-service"] = "hosted-review"
        return response


http = pipelines.asgi(
    lifespan=resources,
    ui=os.environ.get("ANYO_UI_ENABLED") == "1",
    queue_url=os.environ.get("ANYO_QUEUE_URL", "sqlite:///.anyo/queue.sqlite3"),
    queue_namespace="hosted-review",
)
http.routes.append(Route("/service", service))
http.add_middleware(ResponseHeader)


@asynccontextmanager
async def host_lifespan(host: Starlette) -> AsyncIterator[None]:
    async with anyo.lifespan(http):
        yield


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = Starlette(routes=[Route("/health", health), Mount("/anyo", http)], lifespan=host_lifespan)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    anyo.serve(
        app,
        host=os.environ.get("ANYO_HOST", "127.0.0.1"),
        port=int(os.environ.get("ANYO_PORT", "8000")),
    )
