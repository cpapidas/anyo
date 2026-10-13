"""Validated Git inputs and bounded, supervised subprocess ownership."""

from __future__ import annotations

import errno
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from anyo.observability import notify_process_started, notify_process_stopped, register_secrets


class RepositoryError(RuntimeError):
    """Sanitized repository failure with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class HTTPSCredentials:
    """HTTPS username/password or token; supplied only to the Git child environment."""

    username: str = field(repr=False)
    password: str = field(repr=False)
    ca_file: Path | None = None

    def __post_init__(self) -> None:
        for value in (self.username, self.password):
            if not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
                raise ValueError("HTTPS credentials must be nonempty single-line strings")
        if self.ca_file is not None:
            path = Path(self.ca_file).absolute()
            if any(c in str(path) for c in "\r\n\0"):
                raise ValueError("Invalid HTTPS certificate path")
            object.__setattr__(self, "ca_file", path)


@dataclass(frozen=True)
class SSHCredentials:
    """Explicit SSH identity and host trust, without ambient SSH configuration."""

    known_hosts: Path
    username: str = "git"
    identity_file: Path | None = field(default=None, repr=False)
    agent_socket: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.identity_file is None and self.agent_socket is None:
            raise ValueError("Select an SSH identity_file or agent_socket explicitly")
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", self.username):
            raise ValueError("Invalid SSH username")
        for name in ("known_hosts", "identity_file", "agent_socket"):
            value = getattr(self, name)
            if value is not None:
                path = Path(value).absolute()
                if any(c in str(path) for c in "\r\n\0"):
                    raise ValueError("Invalid SSH path")
                object.__setattr__(self, name, path)


Credentials = HTTPSCredentials | SSHCredentials


def source_url(url: str | None, local_path: str | Path | None) -> tuple[str, bool]:
    if (url is None) == (local_path is None):
        raise ValueError("Supply exactly one of url or local_path")
    if local_path is not None:
        path = Path(local_path).absolute()
        if any(c in str(path) for c in "\r\n\0"):
            raise ValueError("Invalid local repository path")
        return str(path), True
    assert url is not None
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("Invalid repository URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid repository URL") from None
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or parsed.hostname.startswith("-")
        or "\\" in url
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path in {"", "/"}
        or port == 0
        or "%" in parsed.netloc
    ):
        raise ValueError("Use an HTTPS or SSH URL without credentials, query, or fragment")
    return url, False


def validate_ref(ref: str) -> None:
    if (
        not isinstance(ref, str)
        or not ref.startswith("refs/")
        or ref.startswith("refs/anyo/")
        or any(c.isspace() or ord(c) < 32 or c in "~^:?*[\\" for c in ref)
        or ".." in ref
        or "@{" in ref
        or any(
            not part or part.startswith(".") or part.endswith((".", ".lock"))
            for part in ref.split("/")
        )
    ):
        raise ValueError("Use a full Git ref, such as refs/heads/main")


def check_deadline(deadline: float, cancel: threading.Event) -> None:
    if cancel.is_set():
        raise RepositoryError("cancelled", "Repository preparation was cancelled")
    if time.monotonic() >= deadline:
        raise RepositoryError("timeout", "Repository operation exceeded its deadline")


def storage_error(error: OSError) -> RepositoryError:
    if error.errno in {errno.ENOSPC, errno.EDQUOT}:
        return RepositoryError("storage_full", "Repository storage has insufficient space")
    return RepositoryError(
        "storage_unavailable", "Repository storage is unavailable or not writable"
    )


def _kill_group(pid: int) -> None:
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except PermissionError as error:
            # Darwin reports EPERM for a group containing only unreaped zombies.
            # Give the reaper a bounded chance to remove it; preserve real denials.
            delay = min(0.01, deadline - time.monotonic())
            if sys.platform != "darwin" or error.errno != errno.EPERM or delay <= 0:
                raise
            time.sleep(delay)


class Git:
    def __init__(
        self,
        executable: str,
        deadline: float,
        cancel: threading.Event,
        *,
        local: bool = False,
        credentials: Credentials | None = None,
    ) -> None:
        self.executable = executable
        self.deadline = deadline
        self.cancel = cancel
        self.fds: list[int] = []
        self.env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        self.options = [
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "core.attributesFile=" + os.devnull,
            "-c",
            "credential.helper=",
            "-c",
            "credential.interactive=false",
            "-c",
            "http.sslVerify=true",
            "-c",
            "http.followRedirects=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "protocol.ssh.allow=always",
            "-c",
            "protocol.file.allow=" + ("always" if local else "never"),
            "-c",
            "submodule.recurse=false",
            "-c",
            "fetch.recurseSubmodules=false",
            "-c",
            "fetch.writeCommitGraph=false",
            "-c",
            "fetch.fsckObjects=true",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            "-c",
            "gc.autoDetach=false",
        ]
        if isinstance(credentials, HTTPSCredentials):
            register_secrets((credentials.username, credentials.password))
            self.env["ANYO_GIT_USERNAME"] = credentials.username
            self.env["ANYO_GIT_PASSWORD"] = credentials.password
            if credentials.ca_file is not None:
                self.env["GIT_SSL_CAINFO"] = str(credentials.ca_file)
            helper = shlex.join([sys.executable, str(Path(__file__).with_name("_credential.py"))])
            self.options += ["-c", "credential.helper=!" + helper]
        if isinstance(credentials, SSHCredentials):
            command = [
                "ssh",
                "-F",
                os.devnull,
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "GlobalKnownHostsFile=" + os.devnull,
                "-o",
                'UserKnownHostsFile="'
                + str(credentials.known_hosts).replace("\\", "\\\\").replace('"', '\\"')
                + '"',
                "-o",
                "IdentityFile=none",
                "-o",
                "IdentitiesOnly=" + ("yes" if credentials.identity_file is not None else "no"),
                "-l",
                credentials.username,
            ]
            if credentials.identity_file is not None:
                command += ["-i", str(credentials.identity_file)]
            if credentials.agent_socket is not None:
                self.env["SSH_AUTH_SOCK"] = str(credentials.agent_socket)
            else:
                command += ["-o", "IdentityAgent=none"]
            self.env["GIT_SSH_COMMAND"] = shlex.join(command)
            self.env["GIT_SSH_VARIANT"] = "ssh"
        else:
            # Public HTTPS/local operations cannot silently fall back to ambient SSH identity.
            self.env["GIT_SSH_COMMAND"] = "false"

    def run(
        self, *args: str, cwd: Path | None = None, allow_failure: bool = False
    ) -> tuple[int, str]:
        check_deadline(self.deadline, self.cancel)
        output, errors = bytearray(), bytearray()
        overflow = threading.Event()

        def drain(pipe: BinaryIO, target: bytearray) -> None:
            try:
                while chunk := pipe.read(65536):
                    target.extend(chunk)
                    if len(target) > 4 * 1024 * 1024:
                        del target[: len(target) - 4 * 1024 * 1024]
                        overflow.set()
            finally:
                pipe.close()

        try:
            process = subprocess.Popen(
                [self.executable, *self.options, *args],
                cwd=cwd,
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=tuple(self.fds),
            )
        except FileNotFoundError:
            raise RepositoryError(
                "git_missing", "Install Git 2.39 or later, or set executable"
            ) from None
        except OSError as error:
            raise storage_error(error) from None
        readers = [
            threading.Thread(target=drain, args=(process.stdout, output), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, errors), daemon=True),
        ]
        try:
            notify_process_started(process.pid)
            for reader in readers:
                reader.start()
            while process.poll() is None:
                check_deadline(self.deadline, self.cancel)
                if overflow.is_set():
                    raise RepositoryError(
                        "output_limit", "Git output exceeded the preparation limit"
                    )
                try:
                    process.wait(timeout=min(0.05, max(0.001, self.deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            _kill_group(process.pid)
            process.wait()
            for reader in readers:
                if reader.ident is not None:
                    reader.join()
            notify_process_stopped(process.pid)
        if overflow.is_set():
            raise RepositoryError("output_limit", "Git output exceeded the preparation limit")
        if process.returncode and not allow_failure:
            message = errors.decode(errors="replace").lower()
            if any(
                word in message
                for word in (
                    "authentication",
                    "could not read username",
                    "permission denied",
                    "401",
                    "403",
                )
            ):
                raise RepositoryError(
                    "authentication_failed",
                    "Git authentication failed; check repository credentials",
                )
            if any(
                word in message
                for word in (
                    "unable to access",
                    "could not resolve",
                    "connection refused",
                    "host key verification",
                )
            ):
                raise RepositoryError(
                    "remote_unavailable", "Cannot reach or verify the repository server"
                )
            if "no space left" in message or "disk quota" in message:
                raise RepositoryError("storage_full", "Git exhausted repository storage")
            raise RepositoryError(
                "git_failed", "Git operation failed; check the remote, revision, and cache"
            )
        return process.returncode, output.decode("utf-8", errors="replace").strip()

    def verify_version(self) -> None:
        _, version = self.run("--version")
        match = re.search(r"git version (\d+)\.(\d+)", version)
        if match is None or (int(match[1]), int(match[2])) < (2, 39):
            raise RepositoryError("git_version", "Repository workspaces require Git 2.39 or later")
