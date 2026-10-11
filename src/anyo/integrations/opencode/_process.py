"""Own one authenticated loopback server and its private state."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import signal
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from anyo.agents import _finish_cleanup
from anyo.integrations.opencode._configuration import Settings
from anyo.observability import (
    DiagnosticStream,
    emit_log,
    log_scope,
    model_environment,
    notify_process_started,
    notify_process_stopped,
    register_secrets,
)
from anyo.prompts import JSONValue, json_text


class OpenCodeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


def native_config(settings: Settings) -> dict[str, JSONValue]:
    providers: dict[str, JSONValue] = {}
    for model in settings.models:
        provider, name = model.model.split("/", 1)
        options: dict[str, JSONValue] = {"apiKey": model.key}
        if model.base_url:
            options["baseURL"] = model.base_url
        previous = providers.get(provider)
        allowed: list[JSONValue] = []
        prior_list = previous.get("whitelist") if isinstance(previous, dict) else None
        if isinstance(prior_list, list):
            allowed = list(prior_list)
        providers[provider] = {"options": options, "whitelist": [*allowed, name]}
    permissions: dict[str, JSONValue] = dict(settings.permissions)
    return {
        "model": settings.models[0].model,
        "small_model": settings.models[0].model,
        "enabled_providers": list(providers),
        "provider": providers,
        "plugin": list(settings.plugins),
        "share": "disabled",
        "autoupdate": False,
        "snapshot": False,
        "permission": permissions,
        "default_agent": "anyo",
        "agent": {"anyo": {"mode": "primary", "permission": permissions}},
    }


class ServerProcess:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self.state: tempfile.TemporaryDirectory[str] | None = None
        self.readers: list[asyncio.Task[None]] = []
        self.password = settings.server_password or secrets.token_urlsafe(32)
        self.url = settings.server_url
        self.plugin_failed = False

    @property
    def alive(self) -> bool:
        return self.settings.server_url is not None or (
            self.process is not None and self.process.returncode is None
        )

    async def start(self) -> str:
        register_secrets((self.password, *(model.key for model in self.settings.models)))
        if self.settings.server_url:
            emit_log(
                "Raw server stdout/stderr unavailable for an externally managed server",
                source="opencode.coverage",
            )
            return self.settings.server_url
        if os.name != "posix":
            raise OpenCodeError("unsupported_platform", "Managed OpenCode requires Linux or macOS")
        if self.settings.state_directory:
            self.settings.state_directory.mkdir(parents=True, exist_ok=True)
        self.state = tempfile.TemporaryDirectory(
            prefix="anyo-opencode-", dir=self.settings.state_directory
        )
        root = Path(self.state.name)
        env = model_environment(
            {k: v for k, v in os.environ.items() if not k.startswith("OPENCODE_")}
        )
        for label in ("config", "data", "cache", "state"):
            directory = root / label
            directory.mkdir(mode=0o700)
            env[f"XDG_{label.upper()}_HOME"] = str(directory)
        # The pinned executable exposes this explicit home-lookup override. It
        # prevents automatic ~/.opencode discovery without changing the host HOME.
        env.update(
            {
                "OPENCODE_TEST_HOME": str(root),
                "OPENCODE_CONFIG_CONTENT": json_text(native_config(self.settings)),
                "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
                "OPENCODE_DISABLE_CLAUDE_CODE": "true",
                "OPENCODE_DISABLE_AUTOUPDATE": "true",
                "OPENCODE_SERVER_PASSWORD": self.password,
                "OPENCODE_SERVER_USERNAME": self.settings.server_username,
            }
        )
        ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def drain(stream: asyncio.StreamReader, stdout: bool) -> None:
            pending = b""
            diagnostics = DiagnosticStream(
                "opencode.stdout" if stdout else "opencode.stderr",
                level="INFO" if stdout else "ERROR",
            )
            try:
                while chunk := await stream.read(4096):
                    diagnostics.feed(chunk)
                    pending = (pending + chunk)[-65536:]
                    lines = pending.split(b"\n")
                    pending = lines.pop()
                    for line in lines:
                        text = line.decode("utf-8", errors="replace")
                        if any(
                            marker in text.lower()
                            for marker in (
                                "failed to load plugin",
                                "failed to install plugin",
                                "plugin config hook failed",
                            )
                        ):
                            self.plugin_failed = True
                        if stdout and not ready.done():
                            match = re.search(r"opencode server listening on (http://\S+)", text)
                            if match:
                                candidate = match.group(1)
                                parsed = urlsplit(candidate)
                                if parsed.hostname == "127.0.0.1" and parsed.port:
                                    ready.set_result(candidate)
            finally:
                diagnostics.feed(b"", final=True)
            if stdout and not ready.done():
                ready.set_exception(
                    OpenCodeError("startup_failed", "OpenCode exited during startup")
                )

        async def spawn() -> None:
            self.process = await asyncio.create_subprocess_exec(
                self.settings.executable,
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                "0",
                "--print-logs",
                cwd=root,
                env=env,
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            notify_process_started(self.process.pid)
            assert self.process.stdout is not None and self.process.stderr is not None
            # One owned server serves several concurrent prompts. Its raw lines
            # do not carry reliable session identity, so never label them with
            # the first caller's run or prompt ID.
            with log_scope(clear=True):
                self.readers = [
                    asyncio.create_task(drain(self.process.stdout, True)),
                    asyncio.create_task(drain(self.process.stderr, False)),
                ]

        try:
            await _finish_cleanup(spawn())
            async with asyncio.timeout(self.settings.startup_timeout):
                self.url = await ready
            return self.url
        except FileNotFoundError:
            await self.close()
            raise OpenCodeError(
                "executable_missing", "Install OpenCode 1.18.34 or set executable"
            ) from None
        except TimeoutError:
            await self.close()
            raise OpenCodeError(
                "startup_timeout", "OpenCode did not become ready in time"
            ) from None
        except BaseException:
            await _finish_cleanup(self.close())
            raise

    async def close(self) -> None:
        process = self.process
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(self.settings.shutdown_timeout):
                    await process.wait()
            except TimeoutError:
                pass
            finally:
                # Kill remaining tool grandchildren even if their parent exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                notify_process_stopped(process.pid)
        if self.readers:
            try:
                async with asyncio.timeout(self.settings.shutdown_timeout):
                    await asyncio.gather(*self.readers, return_exceptions=True)
            except TimeoutError:
                for reader in self.readers:
                    reader.cancel()
                await asyncio.gather(*self.readers, return_exceptions=True)
        self.readers.clear()
        self.process = None
        self.url = self.settings.server_url
        if self.state:
            self.state.cleanup()
            self.state = None
