"""Application registration, webhook reception, and server lifecycle."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import re
import signal
import socket
import threading
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

import uvicorn
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from anyo.agents import Harness, HarnessBuilder, prompt_scope
from anyo.execution import (
    CapacityError,
    Dispatcher,
    QueueFactory,
    QueueMismatch,
    QueueSettings,
    QueueUnavailable,
    ResponseDispatcher,
)
from anyo.execution._codec import EventCodecRegistry
from anyo.execution._operations import OperationsSettings
from anyo.execution._worker import DurableDispatcher
from anyo.hooks import (
    EventCodec,
    EventCodecProvider,
    Hook,
    HookEvent,
    Pipeline,
    Provider,
    Responder,
    ResponseProvider,
    RetryPolicy,
    WebhookDelivery,
    WebhookError,
)
from anyo.ui._server import Dashboard, UICredentials

__all__ = ["Application"]

logger = logging.getLogger(__name__)


def _operations(
    enabled: bool | OperationsSettings, *, ui: bool = False
) -> OperationsSettings | None:
    if type(ui) is not bool:
        raise TypeError("ui must be a boolean")
    if isinstance(enabled, OperationsSettings):
        return enabled
    if type(enabled) is not bool:
        raise TypeError("operations must be a boolean or OperationsSettings")
    return OperationsSettings() if enabled or ui else None


@dataclass(frozen=True)
class _Settings:
    workers: int
    capacity: int
    retention_seconds: float
    max_records: int
    max_body_bytes: int
    request_timeout: float
    queue_url: str = "memory://"
    queue_namespace: str = "default"
    role: str = "all"
    lease_seconds: float = 30
    poll_interval: float = 0.1
    operation_timeout: float = 2
    ui: bool = False
    operations: OperationsSettings | None = None
    execution_mode: Literal["thread", "process"] = "thread"

    def queue_settings(self) -> QueueSettings:
        return QueueSettings(
            capacity=self.capacity,
            max_records=self.max_records,
            retention_seconds=self.retention_seconds,
            lease_seconds=self.lease_seconds,
            poll_interval=self.poll_interval,
            operation_timeout=self.operation_timeout,
        )

    def dispatcher(self) -> Dispatcher:
        return Dispatcher(
            workers=self.workers,
            capacity=self.capacity,
            retention_seconds=self.retention_seconds,
            max_records=self.max_records,
        )

    def validate(self) -> None:
        self.dispatcher()  # Validate execution settings before opening a socket.
        self.queue_settings()
        if not isinstance(self.queue_url, str) or not self.queue_url:
            raise ValueError("queue_url must be a nonempty connection URL")
        if not isinstance(self.queue_namespace, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.queue_namespace
        ):
            raise ValueError("queue_namespace must be a URL-safe name of at most 128 characters")
        if self.role not in {"all", "receiver", "worker"}:
            raise ValueError("role must be all, receiver, or worker")
        if self.queue_url == "memory://" and self.role != "all":
            raise ValueError("Separate receiver/worker roles require SQLite or Redis")
        if self.execution_mode not in {"thread", "process"}:
            raise ValueError("execution_mode must be thread or process")
        if self.queue_url == "memory://" and (
            self.operations is not None or self.execution_mode == "process"
        ):
            raise ValueError("Operations and process execution require SQLite or Redis")
        if self.ui and self.role == "worker":
            raise ValueError("Workers have no HTTP UI; use operations=True on workers")
        if self.ui:
            UICredentials.from_environment()
        if type(self.max_body_bytes) is not int or self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive integer")
        if (
            isinstance(self.request_timeout, bool)
            or not isinstance(self.request_timeout, (int, float))
            or not math.isfinite(self.request_timeout)
            or self.request_timeout <= 0
        ):
            raise ValueError("request_timeout must be a finite positive number")


class Application:
    """An isolated collection of hooks and their provider adapters.

    Constructing an application and registering hooks has no network effects.
    ``start`` owns a blocking server; ``asgi`` delegates serving to an ASGI host.
    The advanced constructor retains the legacy memory default. Supply concrete
    queue factories and a default URL to enable durable queues. The package-root
    convenience application composes SQLite and Redis and defaults to SQLite.
    """

    def __init__(
        self,
        *,
        providers: Iterable[Provider] = (),
        harnesses: Mapping[str, HarnessBuilder] | None = None,
        queue_backends: Mapping[str, QueueFactory] | None = None,
        queue_url: str = "memory://",
        event_codecs: Iterable[EventCodec] = (),
    ) -> None:
        self._queue_backends = dict(queue_backends or {})
        self._queue_url = queue_url
        self._harnesses = dict(harnesses or {})
        self._harness: Harness | None = None
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", provider.name):
                raise ValueError("Provider names must be lowercase URL-safe identifiers")
            if provider.name in self._providers:
                raise ValueError(f"Provider already registered: {provider.name}")
            if isinstance(provider, ResponseProvider):
                deadline = provider.request_timeout
                if (
                    isinstance(deadline, bool)
                    or not isinstance(deadline, (int, float))
                    or not math.isfinite(deadline)
                    or deadline <= 0
                ):
                    raise ValueError("Provider request_timeout must be finite and positive")
            self._providers[provider.name] = provider
        self._codecs = EventCodecRegistry(
            [
                *(
                    provider.event_codec
                    for provider in self._providers.values()
                    if isinstance(provider, EventCodecProvider)
                ),
                *event_codecs,
            ]
        )
        self._hooks: list[Hook] = []
        self._lock = threading.Lock()
        self._active = False

    def harness(
        self,
        *,
        provider: str,
        models: Iterable[Mapping[str, str]],
        plugins: Iterable[str] = (),
        **configuration: object,
    ) -> Harness:
        """Register one inert harness before startup. Builders are explicitly wired."""
        with self._lock:
            if self._active or self._harness is not None:
                raise RuntimeError("Harness already configured or application is running")
            builder = self._harnesses.get(provider)
            if builder is None:
                raise ValueError("Unknown harness provider; register its builder on Application")
            result = builder(models=models, plugins=plugins, **configuration)
            self._harness = result
            return result

    def reset_harness(self) -> None:
        """Remove harness configuration while stopped; never modifies a running client."""
        with self._lock:
            if self._active:
                raise RuntimeError("Cannot reset the harness while the application is running")
            self._harness = None

    def add_hook(
        self,
        *,
        provider: str,
        action: str,
        pipeline: Pipeline | None = None,
        secret: str | None = None,
        responder: Responder | None = None,
        hook_id: str | None = None,
        hook_version: str = "1",
        retry: RetryPolicy | None = None,
        **configuration: object,
    ) -> Hook:
        """Register a callback accepting one ``HookEvent`` and return its hook.

        Provider configuration is validated immediately. A missing ``secret``
        is resolved from the adapter's environment variable at startup. An exact
        duplicate raises ``ValueError``; registration while running raises
        ``RuntimeError``. Pipeline results are discarded. Response-aware
        providers also accept ``responder``, an async callable returning
        HookResponse. A responder-only registration may omit ``pipeline``.
        """
        with self._lock:
            if self._active:
                raise RuntimeError("Cannot register hooks while the application is running")
            if not isinstance(provider, str) or provider not in self._providers:
                raise ValueError("Unknown trigger provider")
            if not isinstance(action, str) or not action:
                raise ValueError("action must be a nonempty string")
            if hook_id is not None and (
                not isinstance(hook_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", hook_id)
            ):
                raise ValueError("hook_id must be a stable URL-safe name of at most 128 characters")
            if not isinstance(hook_version, str) or not hook_version or len(hook_version) > 128:
                raise ValueError("hook_version must be a nonempty string of at most 128 characters")
            if retry is not None and not isinstance(retry, RetryPolicy):
                raise TypeError("retry must be RetryPolicy")
            if pipeline is None and responder is None:
                raise TypeError("pipeline or responder must be callable and accept one event")
            for label, callback in (("pipeline", pipeline), ("responder", responder)):
                if callback is None:
                    continue
                if not callable(callback):
                    raise TypeError(f"{label} must be callable and accept one event argument")
                try:
                    inspect.signature(callback).bind(object())
                except (TypeError, ValueError):
                    raise TypeError(f"{label} must accept one positional event argument") from None
            if responder is not None:
                if not isinstance(self._providers[provider], ResponseProvider):
                    raise ValueError("Provider does not support immediate responders")
                if not (
                    inspect.iscoroutinefunction(responder)
                    or inspect.iscoroutinefunction(getattr(responder, "__call__", None))
                ):
                    raise TypeError("responder must be an async callable")
            if secret is not None and (not isinstance(secret, str) or not secret.strip()):
                raise ValueError("secret must be a nonempty string or None")
            config = dict(self._providers[provider].validate(action, configuration))
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in config.items()):
                raise TypeError("Provider configuration must normalize to string keys and values")
            for hook in self._hooks:
                if hook_id is not None and hook.stable_key == hook_id:
                    raise ValueError("This hook_id is already registered")
                if (
                    hook.provider == provider
                    and hook.action == action
                    and hook.configuration == config
                    and hook.pipeline == pipeline
                    and hook.responder == responder
                    and hook.secret == secret
                ):
                    raise ValueError("This hook is already registered")
            hook = Hook(
                id=f"hook-{len(self._hooks) + 1}",
                provider=provider,
                action=action,
                configuration=MappingProxyType(config),
                pipeline=pipeline,
                secret=secret,
                responder=responder,
                stable_key=hook_id,
                version=hook_version,
                retry=retry or RetryPolicy(),
            )
            self._hooks.append(hook)
            return hook

    def _claim(self, *, require_secrets: bool = True) -> tuple[tuple[Hook, str], ...]:
        with self._lock:
            if self._active:
                raise RuntimeError("The application is already running")
            if not self._hooks:
                raise ValueError("Register at least one hook before starting Anyo")
            prepared = []
            for hook in self._hooks:
                if not require_secrets:
                    prepared.append((hook, ""))
                    continue
                env = self._providers[hook.provider].secret_env
                secret = hook.secret if hook.secret is not None else os.environ.get(env)
                if not secret or not secret.strip():
                    raise ValueError(f"Set {env} or provide secret= for {hook.id}")
                prepared.append((hook, secret))
            self._active = True
            return tuple(prepared)

    def _release(self) -> None:
        with self._lock:
            self._active = False

    def asgi(
        self,
        *,
        workers: int = 4,
        capacity: int = 100,
        retention_seconds: float = 3600,
        max_records: int = 10000,
        max_body_bytes: int = 1048576,
        request_timeout: float = 5,
        queue_url: str | None = None,
        queue_namespace: str = "default",
        role: str = "all",
        lease_seconds: float = 30,
        poll_interval: float = 0.1,
        operation_timeout: float = 2,
        ui: bool = False,
        operations: bool | OperationsSettings = False,
        execution_mode: Literal["thread", "process"] = "thread",
    ) -> Starlette:
        """Build an ASGI application; its asyncio lifespan owns the dispatcher.

        This method does not start a server. Use the returned app's lifespan in
        tests and ASGI hosts. Durable history persists across lifespans; memory
        mode resets it. ``app.state.dispatcher`` exposes inspection/wait helpers.
        Role is ``all`` or ``receiver``; worker-only use needs no ASGI host.
        """
        settings = _Settings(
            workers,
            capacity,
            retention_seconds,
            max_records,
            max_body_bytes,
            request_timeout,
            self._queue_url if queue_url is None else queue_url,
            queue_namespace,
            role,
            lease_seconds,
            poll_interval,
            operation_timeout,
            ui,
            _operations(operations, ui=ui),
            execution_mode,
        )
        settings.validate()
        if role == "worker":
            raise ValueError("Use Application.worker() or start(role='worker') without HTTP")
        return self._build_asgi(settings)

    def _dispatcher(self, settings: _Settings, hooks: list[Hook]) -> Dispatcher | DurableDispatcher:
        if settings.queue_url == "memory://":
            return settings.dispatcher()
        scheme = settings.queue_url.split(":", 1)[0]
        factory = self._queue_backends.get(scheme)
        if factory is None:
            raise ValueError(
                "Queue backend is not registered; supply queue_backends on Application"
            )
        backend = factory(settings.queue_url, settings.queue_namespace, settings.queue_settings())
        return DurableDispatcher(
            backend,
            hooks,
            self._codecs,
            workers=settings.workers,
            run_workers=settings.role != "receiver",
            lease_seconds=settings.lease_seconds,
            poll_interval=settings.poll_interval,
            operations=settings.operations,
            execution_mode=settings.execution_mode,
            harness=self._harness,
            namespace=settings.queue_namespace,
            redaction_secrets=tuple(
                secret
                for secret in (
                    os.environ.get("ANYO_UI_PASSWORD", ""),
                    *(
                        tuple(model.key for model in self._harness.models)
                        if self._harness is not None
                        else ()
                    ),
                    *(
                        hook.secret or os.environ.get(self._providers[hook.provider].secret_env, "")
                        for hook in hooks
                    ),
                )
                if secret
            ),
        )

    def _build_asgi(
        self, settings: _Settings, prepared: tuple[tuple[Hook, str], ...] | None = None
    ) -> Starlette:
        dispatcher: Dispatcher | DurableDispatcher | None = None
        responses: ResponseDispatcher | None = None
        registrations: tuple[tuple[Hook, str], ...] = ()

        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            nonlocal dispatcher, responses, registrations
            # A supplied snapshot is already claimed by start(); otherwise this
            # lifespan owns the claim and releases it even when startup fails.
            registrations = self._claim() if prepared is None else prepared
            try:
                current = self._dispatcher(settings, [hook for hook, _ in registrations])
                immediate = ResponseDispatcher(
                    workers=settings.workers,
                    max_records=settings.max_records,
                    retention_seconds=settings.retention_seconds,
                )
                async with prompt_scope(self._harness if settings.role == "all" else None):
                    await current.start()
                    dispatcher = current
                    responses = immediate
                    app.state.dispatcher = current
                    try:
                        yield
                    finally:
                        dispatcher = None
                        responses = None
                        try:
                            await immediate.close()
                        finally:
                            await current.close()
            finally:
                registrations = ()
                if prepared is None:
                    self._release()

        async def receive(request: Request) -> Response:
            if isinstance(dispatcher, DurableDispatcher):
                with dispatcher.log_context():
                    return await handle_delivery(request)
            return await handle_delivery(request)

        async def handle_delivery(request: Request) -> Response:
            name = request.path_params["provider"]
            adapter = self._providers.get(name)
            candidates = [(h, s) for h, s in registrations if h.provider == name]
            if adapter is None:
                return JSONResponse({"error": "Unknown provider"}, status_code=404)
            current = dispatcher
            immediate = responses
            if current is None or immediate is None:
                return JSONResponse({"error": "Application is not accepting deliveries"}, 503)
            if not candidates:
                return JSONResponse({"error": "No hooks for this provider"}, status_code=404)
            try:
                budget = adapter.request_timeout if isinstance(adapter, ResponseProvider) else None
                deadline = (
                    asyncio.get_running_loop().time() + budget if budget is not None else None
                )
                async with asyncio.timeout_at(deadline):
                    return await dispatch(
                        request, adapter, candidates, current, immediate, deadline
                    )
            except TimeoutError:
                return JSONResponse({"error": "Provider response deadline exceeded"}, 503)
            except WebhookError as exc:
                return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
            except CapacityError:
                return JSONResponse(
                    {"error": "Delivery capacity unavailable; redeliver later"}, 503
                )
            except (QueueUnavailable, QueueMismatch):
                return JSONResponse({"error": "Queue unavailable; redeliver later"}, 503)
            except RecursionError:
                return JSONResponse({"error": "Webhook payload nesting is too deep"}, 400)

        async def dispatch(
            request: Request,
            adapter: Provider,
            candidates: list[tuple[Hook, str]],
            current: Dispatcher | DurableDispatcher,
            immediate: ResponseDispatcher,
            deadline: float | None,
        ) -> Response:
            name = adapter.name
            body = await self._read_body(request, settings)
            jobs: list[tuple[Hook, HookEvent]] = []
            responders: list[tuple[Hook, HookEvent]] = []
            delivery: WebhookDelivery | None = None
            decoded: dict[str, HookEvent | WebhookDelivery | None] = {}
            invalid: set[str] = set()
            # Preserve ambiguity for adapters instead of silently choosing the
            # first repeated credential header. Signatures/tokens reject commas.
            headers: dict[str, str] = {}
            for key, value in request.headers.items():
                headers[key] = f"{headers[key]}, {value}" if key in headers else value
            for hook, secret in candidates:
                if secret in invalid:
                    continue
                if secret not in decoded:
                    try:
                        decoded[secret] = adapter.decode(body, headers, secret)
                    except WebhookError as exc:
                        if exc.status_code != 401:
                            raise
                        invalid.add(secret)
                        continue
                result = decoded[secret]
                if isinstance(result, WebhookDelivery):
                    delivery = result
                    event = result.event
                else:
                    event = result
                if (
                    event is not None
                    and event.provider == name
                    and event.action == hook.action
                    and adapter.matches(hook.configuration, event)
                ):
                    if hook.pipeline is not None:
                        jobs.append((hook, event))
                    if hook.responder is not None:
                        responders.append((hook, event))
            if not decoded:
                raise WebhookError(401, "Invalid webhook authentication")
            # Decoding and matching are synchronous. Check their elapsed time
            # explicitly; an asyncio timer cannot interrupt those operations.
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError
            if len(responders) > 1:
                raise WebhookError(409, "Multiple immediate responders match this delivery")
            reply = delivery.response if delivery is not None else None
            if responders:
                if delivery is None or delivery.render is None:
                    raise WebhookError(400, "Provider cannot render an immediate response")
                hook, event = responders[0]
                try:
                    reply = await immediate.respond(hook, event, delivery.render, deadline=deadline)
                except (CapacityError, RecursionError, TimeoutError):
                    raise
                except Exception as error:
                    logger.error("Responder hook %s failed with %s", hook.id, type(error).__name__)
                    raise WebhookError(503, "Immediate responder failed") from None
            elif delivery is not None and delivery.requires_response:
                raise WebhookError(503, "A matching immediate responder is required")
            submitted = jobs if reply is None or reply.dispatch else []
            if isinstance(current, DurableDispatcher):
                try:
                    accepted = await current.submit(submitted, deadline=deadline)
                except QueueMismatch:
                    raise
                except (ValueError, TypeError):
                    raise WebhookError(
                        400, "Event cannot be stored with the registered codecs"
                    ) from None
            else:
                accepted = current.submit(submitted, deadline=deadline)
            if accepted:
                logger.info("Accepted %d pipeline jobs for provider %s", accepted, name)
            if reply is not None:
                return Response(
                    reply.body, status_code=reply.status_code, media_type=reply.media_type
                )
            return JSONResponse({"accepted": accepted}, status_code=202 if accepted else 200)

        routes = [Route("/webhooks/{provider}", receive, methods=["POST"])]
        if settings.ui:
            dashboard = Dashboard(
                UICredentials.from_environment(),
                lambda: dispatcher if isinstance(dispatcher, DurableDispatcher) else None,
                namespace=settings.queue_namespace,
                max_records=settings.max_records,
                stale_seconds=max(5, settings.lease_seconds * 2),
            )
            routes.extend(dashboard.routes())
        return Starlette(routes=routes, lifespan=lifespan)

    @staticmethod
    async def _read_body(request: Request, settings: _Settings) -> bytes:
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 20:
                raise WebhookError(400, "Invalid Content-Length")
            if int(length) > settings.max_body_bytes:
                raise WebhookError(413, "Webhook body exceeds max_body_bytes")
        data = bytearray()
        try:
            async with asyncio.timeout(settings.request_timeout):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > settings.max_body_bytes:
                        raise WebhookError(413, "Webhook body exceeds max_body_bytes")
                    data.extend(chunk)
        except TimeoutError:
            raise WebhookError(408, "Webhook body read timed out") from None
        except ClientDisconnect:
            raise WebhookError(400, "Webhook client disconnected") from None
        return bytes(data)

    def start(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        workers: int = 4,
        capacity: int = 100,
        retention_seconds: float = 3600,
        max_records: int = 10000,
        max_body_bytes: int = 1048576,
        request_timeout: float = 5,
        queue_url: str | None = None,
        queue_namespace: str = "default",
        role: str = "all",
        lease_seconds: float = 30,
        poll_interval: float = 0.1,
        operation_timeout: float = 2,
        ui: bool = False,
        operations: bool | OperationsSettings = False,
        execution_mode: Literal["thread", "process"] = "thread",
    ) -> None:
        """Serve webhooks or pull jobs until interrupted.

        ``workers`` limits concurrent callbacks, and ``capacity`` bounds queued
        plus running jobs. Terminal delivery history expires after
        ``retention_seconds``; ``max_records`` bounds retained records. Durable
        queues share limits/history across the namespace. Durable shutdown stops
        claiming and finishes owned active attempts, preserving unclaimed backlog;
        memory mode instead drains its queue and loses history on restart.
        Body reads are bounded by
        ``max_body_bytes`` and ``request_timeout`` seconds. Callback duration is
        not limited: callers must set deadlines on their own external work.

        Raises configuration errors before serving, ``OSError`` on binding
        failure, and ``RuntimeError`` for concurrent starts or an existing event
        loop. Use ``asgi`` with an existing asyncio ASGI host instead.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("start() cannot run inside an event loop; use Application.asgi()")
        if role == "worker":
            if ui:
                raise ValueError("Workers have no HTTP UI; use operations=True on workers")
            asyncio.run(
                self._serve_worker(
                    queue_url=queue_url,
                    queue_namespace=queue_namespace,
                    workers=workers,
                    capacity=capacity,
                    retention_seconds=retention_seconds,
                    max_records=max_records,
                    lease_seconds=lease_seconds,
                    poll_interval=poll_interval,
                    operation_timeout=operation_timeout,
                    operations=operations,
                    execution_mode=execution_mode,
                )
            )
            return
        if not isinstance(host, str) or not host.strip():
            raise ValueError("host must be a nonempty string")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        settings = _Settings(
            workers,
            capacity,
            retention_seconds,
            max_records,
            max_body_bytes,
            request_timeout,
            self._queue_url if queue_url is None else queue_url,
            queue_namespace,
            role,
            lease_seconds,
            poll_interval,
            operation_timeout,
            ui,
            _operations(operations, ui=ui),
            execution_mode,
        )
        settings.validate()
        prepared = self._claim()
        try:
            app = self._build_asgi(settings, prepared)
            # Bind ourselves so failures raise OSError instead of Uvicorn's
            # command-line SystemExit, and always close our owned socket.
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            with socket.create_server((host, port), family=family) as listener:
                config = uvicorn.Config(
                    app,
                    log_config=None,
                    access_log=False,
                    loop="asyncio",
                    lifespan="on",
                    ws="none",
                    proxy_headers=False,
                    server_header=False,
                )
                server = uvicorn.Server(config)
                logger.info("Anyo listening on %s", listener.getsockname())
                try:
                    server.run(sockets=[listener])
                except SystemExit as exc:
                    raise RuntimeError("Anyo server failed to start") from exc
                if not server.started:
                    raise RuntimeError("Anyo server did not start")
        finally:
            self._release()

    @asynccontextmanager
    async def worker(
        self,
        *,
        queue_url: str | None = None,
        queue_namespace: str = "default",
        workers: int = 4,
        capacity: int = 100,
        retention_seconds: float = 3600,
        max_records: int = 10000,
        lease_seconds: float = 30,
        poll_interval: float = 0.1,
        operation_timeout: float = 2,
        operations: bool | OperationsSettings = False,
        execution_mode: Literal["thread", "process"] = "thread",
    ) -> AsyncIterator[DurableDispatcher]:
        """Own a worker without HTTP or signing secrets; close drains owned attempts.

        Consumers may await ``wait_idle()``, inspect records, or retry a terminal
        job through the yielded runner. Pending backlog remains in the backend.
        """
        settings = _Settings(
            workers,
            capacity,
            retention_seconds,
            max_records,
            1048576,
            5,
            self._queue_url if queue_url is None else queue_url,
            queue_namespace,
            "worker",
            lease_seconds,
            poll_interval,
            operation_timeout,
            False,
            _operations(operations),
            execution_mode,
        )
        settings.validate()
        registrations = self._claim(require_secrets=False)
        try:
            if not any(hook.pipeline is not None for hook, _ in registrations):
                raise ValueError("Register a pipeline before starting a worker")
            current = self._dispatcher(settings, [hook for hook, _ in registrations])
            assert isinstance(current, DurableDispatcher)
            async with prompt_scope(self._harness):
                await current.start()
                try:
                    yield current
                finally:
                    await current.close()
        finally:
            self._release()

    async def _serve_worker(
        self,
        *,
        queue_url: str | None,
        queue_namespace: str,
        workers: int,
        capacity: int,
        retention_seconds: float,
        max_records: int,
        lease_seconds: float,
        poll_interval: float,
        operation_timeout: float,
        operations: bool | OperationsSettings = False,
        execution_mode: Literal["thread", "process"] = "thread",
    ) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        original_handlers = {
            signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(signum, stop.set)
                except (NotImplementedError, RuntimeError):
                    break
                installed.append(signum)
            async with self.worker(
                queue_url=queue_url,
                queue_namespace=queue_namespace,
                workers=workers,
                capacity=capacity,
                retention_seconds=retention_seconds,
                max_records=max_records,
                lease_seconds=lease_seconds,
                poll_interval=poll_interval,
                operation_timeout=operation_timeout,
                operations=operations,
                execution_mode=execution_mode,
            ) as runner:
                logger.info("Anyo worker ready")

                async def failure_result(*, stopped: bool = False) -> BaseException | None:
                    # asyncio treats process-control exceptions escaping child
                    # tasks specially. Carry them to the lifecycle owner as data.
                    try:
                        if stopped:
                            await runner.wait_stopped()
                        else:
                            await runner.wait_failure()
                    except BaseException as error:
                        return error
                    return None

                failing = asyncio.create_task(failure_result())
                waiting = [
                    asyncio.create_task(stop.wait()),
                    failing,
                    asyncio.create_task(failure_result(stopped=True)),
                ]
                try:
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        result = task.result()
                        if isinstance(result, BaseException):
                            raise result
                    logger.info("Anyo worker shutdown requested")
                finally:
                    for task in waiting:
                        task.cancel()
                    await asyncio.gather(*waiting, return_exceptions=True)
        finally:
            for signum in installed:
                loop.remove_signal_handler(signum)
                signal.signal(signum, original_handlers[signum])
