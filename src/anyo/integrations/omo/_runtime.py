"""Bounded classic-stdio RPC against a directly owned native OmO process."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

from anyo.agents import PromptEvent, PromptResult, _finish_cleanup, parse_result, runtime_failure
from anyo.integrations.omo._configuration import VERSION, Settings
from anyo.observability import (
    DiagnosticStream,
    emit_json_log,
    model_environment,
    notify_process_started,
    notify_process_stopped,
    register_secrets,
)
from anyo.prompts import (
    MAX_BYTES,
    JSONValue,
    PromptRequest,
    decode_json,
    envelope_instruction,
    envelope_schema,
    json_text,
)

# Keep user extensions, sidecars, automatic model selection and daemons outside
# the owned invocation. Native OmO's task engine remains available when enabled.
_DISABLED_COMPONENTS = (
    "config-startup",
    "model-profile",
    "bundled-skills",
    "skill-commands",
    "onboarding",
    "telemetry",
    "memory",
    "config-watch",
    "builtin-mcps",
    "lsp",
    "ast-grep",
    "browser-bridge",
    "computer-use",
    "x-search",
    "claude-code",
    "comment-checker",
    "gateway",
    "thread",
    "ulw-execute-continuation",
    "ulw-loop",
)


class OmoError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


def _object(value: JSONValue) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        raise OmoError("protocol_error", "OmO returned an unexpected record")
    return value


class _Process:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self.stderr: asyncio.Task[None] | None = None

    async def start(self, args: list[str], env: dict[str, str], cwd: Path) -> None:
        register_secrets(model.key for model in self.settings.models)

        async def spawn() -> None:
            self.process = await asyncio.create_subprocess_exec(
                self.settings.executable,
                *args,
                cwd=cwd,
                env=env,
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_BYTES + 1,
            )
            notify_process_started(self.process.pid)
            self.stderr = asyncio.create_task(self._capture_stderr())

        await _finish_cleanup(spawn())

    async def _capture_stderr(self) -> None:
        assert self.process and self.process.stderr
        diagnostics = DiagnosticStream("omo.stderr", level="ERROR")
        try:
            while chunk := await self.process.stderr.read(8192):
                diagnostics.feed(chunk)
        finally:
            diagnostics.feed(b"", final=True)

    async def read(self) -> dict[str, JSONValue]:
        assert self.process and self.process.stdout
        try:
            line = await self.process.stdout.readline()
            if not line:
                raise OmoError("process_failed", "OmO exited before completing the invocation")
            return _object(decode_json(line))
        except ValueError:
            raise OmoError("protocol_error", "OmO returned invalid or oversized JSON") from None

    async def send(self, body: dict[str, JSONValue]) -> None:
        assert self.process and self.process.stdin
        try:
            # PromptRequest already bounds the input. JSON escaping can make its
            # wire record larger than that limit without enlarging the prompt.
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False) + "\n"
            self.process.stdin.write(encoded.encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise OmoError("process_failed", "OmO closed its input") from None

    async def _signal_group(self, signum: signal.Signals) -> None:
        assert self.process
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.shutdown_timeout
        while True:
            try:
                os.killpg(self.process.pid, signum)
                return
            except ProcessLookupError:
                return
            except PermissionError:
                # Darwin skips zombies when signalling groups and returns EPERM
                # while a group has only unreaped members. Wait for the reaper,
                # but preserve genuine permission failures after a bounded retry.
                delay = min(0.01, deadline - loop.time())
                if sys.platform != "darwin" or delay <= 0:
                    raise
                await asyncio.sleep(delay)

    async def close(self) -> None:
        process = self.process
        if process:
            if process.stdin:
                process.stdin.close()
            await self._signal_group(signal.SIGTERM)
            try:
                async with asyncio.timeout(self.settings.shutdown_timeout):
                    await process.wait()
            except TimeoutError:
                pass
            finally:
                await self._signal_group(signal.SIGKILL)
                await process.wait()
                notify_process_stopped(process.pid)
        if self.stderr:
            try:
                async with asyncio.timeout(self.settings.shutdown_timeout):
                    await self.stderr
            except TimeoutError:
                self.stderr.cancel()
                await asyncio.gather(self.stderr, return_exceptions=True)


def _prepare(
    settings: Settings, request: PromptRequest, root: Path
) -> tuple[list[str], dict[str, str]]:
    # Preserve host identity, path and transport settings, but do not inherit other
    # providers' keys or an ambient agent configuration. HOME is never rewritten.
    inherited = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )
    env = model_environment({name: os.environ[name] for name in inherited if name in os.environ})
    env.update(
        {
            "OMO_CODING_AGENT_DIR": str(root),
            "OMO_OFFLINE": "1",
            "OMO_TELEMETRY": "0",
            "OMO_SEND_ANONYMOUS_TELEMETRY": "0",
            "OMO_DISABLE_PROCESS_CLEANUP": "1",
            "SENPI_NO_FALLBACK": "1",
            "ANYO_OMO_POLICY": str(root / "policy.json"),
        }
    )
    providers: dict[str, JSONValue] = {}
    for entry in settings.models:
        provider, model = entry.model.split("/", 1)
        if provider not in providers:
            variable = f"ANYO_OMO_KEY_{len(providers)}"
            env[variable] = entry.key
            options: dict[str, JSONValue] = {"apiKey": "${" + variable + "}", "whitelist": []}
            if entry.base_url:
                options["baseUrl"] = entry.base_url
            providers[provider] = options
        item = _object(providers[provider])
        allowed = item["whitelist"]
        assert isinstance(allowed, list)
        allowed.append(model)
    settings_file = {
        "defaultProvider": request.model.split("/", 1)[0],
        "defaultModel": request.model.split("/", 1)[1],
        "retry": {"enabled": False, "modelFallback": False, "provider": {"maxRetries": 0}},
        "compaction": {"enabled": False},
        "packages": [],
        "defaultProjectTrust": "never",
    }
    policy = {
        "models": [m.model for m in settings.models],
        "selected": request.model,
        "tools": list(settings.tools),
        "catalog": str(root / "models.json"),
        "schema": envelope_schema(),
    }
    for name, value in (
        ("settings.json", settings_file),
        ("models.json", {"providers": providers}),
        ("policy.json", policy),
    ):
        path = root / name
        path.write_text(json_text(value), encoding="utf-8")
        path.chmod(0o600)
    bridge = root / "bridge.mjs"
    bridge.write_text(files("anyo.integrations.omo").joinpath("_bridge.mjs").read_text("utf-8"))
    instruction = root / "envelope.txt"
    instruction.write_text(envelope_instruction(), encoding="utf-8")
    args = [
        "--mode",
        "rpc",
        "--no-session",
        "--offline",
        "--no-approve",
        "--no-skills",
        "--no-context-files",
        "--no-prompt-templates",
        "--no-themes",
        "--provider",
        request.model.split("/", 1)[0],
        "--model",
        request.model.split("/", 1)[1],
        "--tools",
        ",".join(dict.fromkeys([*settings.tools, "StructuredOutput"])),
        "--extension",
        str(bridge),
        "--append-system-prompt",
        str(instruction),
        "--no-model-fallback",
        "--no-recommended-models",
        "--no-ask-user",
        "--permission",
        "*=deny",
        "--permission",
        "external_directory=deny",
    ]
    for name in [*settings.tools, "StructuredOutput"]:
        args.extend(["--permission", f"{name}=allow"])
    if {"ls", "find"} & set(settings.tools):
        args.extend(["--permission", "list=allow"])
    args.extend(f"--omo-senpi-{component}-disabled" for component in _DISABLED_COMPONENTS)
    return args, env


class OmoBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.active: set[asyncio.Task[object]] = set()
        self.closed = False

    async def invoke(
        self, request: PromptRequest, emit: Callable[[PromptEvent], None]
    ) -> PromptResult:
        if self.closed:
            raise RuntimeError("Prompt backend is closed")
        if len(self.active) >= self.settings.max_concurrency:
            return runtime_failure(
                request.run_id, "capacity_exceeded", "Prompt capacity is exhausted"
            )
        task = asyncio.current_task()
        assert task is not None
        self.active.add(task)
        process = _Process(self.settings)
        state: tempfile.TemporaryDirectory[str] | None = None
        session: str | None = None

        def progress(kind: str, message: str | None = None) -> None:
            if message:
                for model in self.settings.models:
                    message = message.replace(model.key, "[redacted]")
            emit(PromptEvent(request.run_id, kind, message, session))

        try:
            if os.name != "posix":
                raise OmoError("unsupported_platform", "Managed OmO requires Linux or macOS")
            async with asyncio.timeout(request.timeout):
                if self.settings.state_directory:
                    self.settings.state_directory.mkdir(parents=True, exist_ok=True)
                state = tempfile.TemporaryDirectory(
                    prefix="anyo-omo-", dir=self.settings.state_directory
                )
                root = Path(state.name)
                args, env = _prepare(self.settings, request, root)
                async with asyncio.timeout(self.settings.startup_timeout):
                    await process.start(["--version"], env, root)
                    assert process.process and process.process.stdout
                    try:
                        version = await process.process.stdout.readline()
                    except ValueError:
                        raise OmoError(
                            "version_mismatch", f"Install native OmO {VERSION}"
                        ) from None
                    await process.process.wait()
                    if process.process.returncode != 0 or not version.decode(
                        errors="replace"
                    ).startswith(f"omo {VERSION} "):
                        raise OmoError("version_mismatch", f"Install native OmO {VERSION}")
                    await process.close()
                    process = _Process(self.settings)
                    await process.start(args, env, request.workspace)
                    await process.send({"id": "anyo-start", "type": "get_state"})
                    ready = False
                    while True:
                        record = await process.read()
                        self._error(record)
                        if record.get("type") == "anyo.ready":
                            if record.get("model") != request.model:
                                raise OmoError(
                                    "configuration_mismatch", "OmO selected a different model"
                                )
                            ready = True
                        if record.get("type") == "response" and record.get("id") == "anyo-start":
                            data = _object(record.get("data"))
                            model = _object(data.get("model"))
                            if (
                                not ready
                                or f"{model.get('provider')}/{model.get('id')}" != request.model
                            ):
                                raise OmoError(
                                    "configuration_mismatch", "OmO did not confirm the harness"
                                )
                            sid = data.get("sessionId")
                            if not isinstance(sid, str) or not sid:
                                raise OmoError("protocol_error", "OmO returned no session ID")
                            session = sid
                            break
                await process.send({"id": "anyo-prompt", "type": "prompt", "message": request.text})
                progress("status", "submitted")
                result: PromptResult | None = None
                while True:
                    record = await process.read()
                    self._error(record)
                    kind = record.get("type")
                    if kind == "anyo.model":
                        model_id = record.get("model")
                        if model_id not in {m.model for m in self.settings.models}:
                            raise OmoError(
                                "configuration_mismatch", "OmO used an unregistered model"
                            )
                        progress("model", str(model_id))
                    elif kind == "agent_start":
                        progress("status", "busy")
                    elif kind == "message_update":
                        update = _object(record.get("assistantMessageEvent"))
                        delta = update.get("delta")
                        if isinstance(delta, str):
                            if update.get("type") == "text_delta":
                                progress("text_delta", delta)
                            elif (
                                update.get("type") == "thinking_delta" and request.include_reasoning
                            ):
                                progress("reasoning_delta", delta)
                    elif kind in {
                        "tool_execution_start",
                        "tool_execution_end",
                        "tool_execution_update",
                    }:
                        tool = record.get("toolName")
                        suffix = {
                            "tool_execution_start": "start",
                            "tool_execution_end": "end",
                            "tool_execution_update": "update",
                        }[str(kind)]
                        progress("tool_" + suffix, str(tool))
                        if tool != "StructuredOutput":
                            emit_json_log(record, source="omo.tools")
                        if (
                            kind == "tool_execution_end"
                            and tool == "StructuredOutput"
                            and not record.get("isError")
                        ):
                            details = _object(_object(record.get("result")).get("details"))
                            try:
                                result = parse_result(
                                    details.get("anyo_result"),
                                    request.run_id,
                                    session,
                                    str(record.get("toolCallId", "result")),
                                )
                            except ValueError:
                                raise OmoError(
                                    "invalid_output", "OmO returned an invalid envelope"
                                ) from None
                    elif kind == "message_end":
                        message = _object(record.get("message"))
                        if message.get("role") == "assistant" and message.get("stopReason") in {
                            "error",
                            "aborted",
                        }:
                            emit_json_log(message, source="omo.diagnostics", level="ERROR")
                            raise OmoError("provider_error", "OmO reported a provider error")
                    elif kind == "agent_end" and not record.get("willRetry"):
                        if result is None:
                            raise OmoError("invalid_output", "OmO returned no structured result")
                        progress("status", "idle")
                        return result
        except TimeoutError:
            return runtime_failure(
                request.run_id, "timeout", "OmO invocation exceeded its deadline", session
            )
        except FileNotFoundError:
            return runtime_failure(
                request.run_id,
                "executable_missing",
                f"Install native OmO {VERSION} or set executable",
                session,
            )
        except OmoError as error:
            return runtime_failure(request.run_id, error.code, error.message, session)
        except OSError:
            return runtime_failure(
                request.run_id, "process_failed", "Could not run native OmO", session
            )
        finally:
            try:
                await _finish_cleanup(process.close())
            finally:
                if state:
                    state.cleanup()
                self.active.discard(task)

    @staticmethod
    def _error(record: dict[str, JSONValue]) -> None:
        if record.get("type") == "anyo.error":
            emit_json_log(record, source="omo.diagnostics", level="ERROR")
            code = record.get("code")
            if code not in {"configuration_failed", "configuration_mismatch"}:
                code = "configuration_failed"
            raise OmoError(str(code), "Native OmO rejected the harness configuration")
        if record.get("type") == "response" and record.get("success") is False:
            emit_json_log(record, source="omo.diagnostics", level="ERROR")
            raise OmoError("request_rejected", "OmO rejected a control request")
        if record.get("type") == "extension_ui_request" and record.get("method") in {
            "select",
            "confirm",
            "input",
            "editor",
            "question",
        }:
            raise OmoError("interaction_required", "OmO requires unattended interaction")

    async def close(self) -> None:
        self.closed = True
        active = list(self.active)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
