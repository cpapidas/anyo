"""Repository acquisition, detached workspace lifetime, and portable bundle seeds."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from anyo.observability import emit_log, private_credential_scope

from ._git import (
    Credentials,
    Git,
    HTTPSCredentials,
    RepositoryError,
    SSHCredentials,
    check_deadline,
    source_url,
    storage_error,
    validate_ref,
)
from ._storage import Lock, digest, directory, disk_size, read_json, write_json


@dataclass(frozen=True)
class RepositoryCheckout:
    """A workspace valid only inside its acquisition context."""

    path: Path
    commit_sha: str
    cache_hit: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaintenanceResult:
    removed_entries: int
    recovered_workspaces: int
    bytes_remaining: int


@dataclass
class _Owned:
    checkout: RepositoryCheckout
    entry: Path
    lease: Lock


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be positive and finite")
    return time.monotonic() + value


def _private_values(credentials: Credentials | None) -> tuple[str, ...]:
    if isinstance(credentials, HTTPSCredentials):
        return (credentials.password,)
    if isinstance(credentials, SSHCredentials):
        return tuple(
            str(path) for path in (credentials.identity_file, credentials.agent_socket) if path
        )
    return ()


class RepositoryCache:
    """A persistent cache on one POSIX host (including containers sharing its volume).

    Construction creates no files or processes. ``namespace`` is an access scope,
    not an authentication boundary. Each checkout is independently writable, but
    its Git objects/configuration are shared with cooperating trusted jobs.
    """

    def __init__(
        self,
        directory: str | Path = ".anyo/repositories",
        *,
        namespace: str = "default",
        executable: str = "git",
        object_format: Literal["sha1", "sha256"] = "sha1",
        idle_seconds: float = 7 * 86400,
        max_bytes: int = 5 * 1024**3,
        min_free_bytes: int = 64 * 1024**2,
    ) -> None:
        if not isinstance(namespace, str) or not namespace.strip() or len(namespace) > 256:
            raise ValueError("namespace must be a nonempty string of at most 256 characters")
        if object_format not in {"sha1", "sha256"}:
            raise ValueError("object_format must be sha1 or sha256")
        if not math.isfinite(idle_seconds) or idle_seconds < 0:
            raise ValueError("idle_seconds must be finite and nonnegative")
        if (
            any(
                isinstance(v, bool) or not isinstance(v, int) or v < 0
                for v in (max_bytes, min_free_bytes)
            )
            or max_bytes == 0
        ):
            raise ValueError("max_bytes must be positive and min_free_bytes nonnegative integers")
        if not executable or "\0" in executable:
            raise ValueError("executable must name a Git binary")
        self.directory = Path(directory).absolute()
        self.namespace = namespace
        self.executable = executable
        self.object_format = object_format
        self.idle_seconds = idle_seconds
        self.max_bytes = max_bytes
        self.min_free_bytes = min_free_bytes

    def _root(self) -> Path:
        if os.name != "posix":
            raise RepositoryError(
                "unsupported_platform", "Repository workspaces require Linux or macOS"
            )
        # Separate access scopes even for maintenance and purge operations.
        scope = hashlib.sha256(self.namespace.encode()).hexdigest()
        root = self.directory / scope
        for path in (self.directory, root, root / "entries", root / "locks"):
            directory(path)
        return root

    def _identity(self, source: str, local: bool) -> dict[str, str | int | bool]:
        return {
            "schema": 1,
            "source": source,
            "local": local,
            "namespace": self.namespace,
            "format": self.object_format,
        }

    def _key(self, source: str, local: bool) -> str:
        return hashlib.sha256(
            json.dumps(self._identity(source, local), sort_keys=True).encode()
        ).hexdigest()

    def _git(
        self,
        deadline: float,
        cancel: threading.Event,
        *,
        local: bool = False,
        credentials: Credentials | None = None,
    ) -> Git:
        return Git(self.executable, deadline, cancel, local=local, credentials=credentials)

    @contextmanager
    def checkout(
        self,
        *,
        url: str | None = None,
        local_path: str | Path | None = None,
        commit: str | None = None,
        ref: str | None = None,
        fetch_ref: str | None = None,
        credentials: Credentials | None = None,
        timeout: float = 120,
    ) -> Iterator[RepositoryCheckout]:
        """Acquire exactly ``commit`` or freshly fetch ``ref``; release on context exit.

        Commits must be full object IDs. ``fetch_ref`` helps find a pinned commit
        (for example a GitHub PR head) and never replaces the requested SHA.
        Network URLs must be trusted HTTPS/SSH configuration. Local repositories
        require explicit ``local_path``. No hooks, submodules, or LFS filters run.
        """
        owned = self._acquire(
            url,
            local_path,
            commit,
            ref,
            fetch_ref,
            credentials,
            _timeout(timeout),
            threading.Event(),
        )
        try:
            with private_credential_scope(_private_values(credentials)):
                yield owned.checkout
        finally:
            self._release(owned)

    @asynccontextmanager
    async def acheckout(
        self,
        *,
        url: str | None = None,
        local_path: str | Path | None = None,
        commit: str | None = None,
        ref: str | None = None,
        fetch_ref: str | None = None,
        credentials: Credentials | None = None,
        timeout: float = 120,
    ) -> AsyncIterator[RepositoryCheckout]:
        """Nonblocking checkout; cancellation stops Git before releasing ownership."""
        cancel = threading.Event()
        task = asyncio.create_task(
            asyncio.to_thread(
                self._acquire,
                url,
                local_path,
                commit,
                ref,
                fetch_ref,
                credentials,
                _timeout(timeout),
                cancel,
            )
        )
        try:
            owned = await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, RepositoryError):
                    pass
            if not task.cancelled() and task.exception() is None:
                await self._async_release(task.result())
            raise
        try:
            with private_credential_scope(_private_values(credentials)):
                yield owned.checkout
        finally:
            await self._async_release(owned)

    async def _async_release(self, owned: _Owned) -> None:
        task = asyncio.create_task(asyncio.to_thread(self._release, owned))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    def _inputs(
        self, url: str | None, local_path: str | Path | None, credentials: Credentials | None
    ) -> tuple[str, bool]:
        source, local = source_url(url, local_path)
        if credentials is not None:
            if local or not isinstance(credentials, (HTTPSCredentials, SSHCredentials)):
                raise ValueError("Credentials apply only to matching HTTPS or SSH URLs")
            if source.startswith("https:") != isinstance(credentials, HTTPSCredentials):
                raise ValueError("Credential type must match the repository URL")
        elif source.startswith("ssh:"):
            raise ValueError("SSH URLs require explicit SSHCredentials and known_hosts")
        return source, local

    def _acquire(
        self,
        url: str | None,
        local_path: str | Path | None,
        commit: str | None,
        ref: str | None,
        fetch_ref: str | None,
        credentials: Credentials | None,
        deadline: float,
        cancel: threading.Event,
    ) -> _Owned:
        source, local = self._inputs(url, local_path, credentials)
        if (commit is None) == (ref is None):
            raise ValueError("Supply exactly one of commit or ref")
        length = 40 if self.object_format == "sha1" else 64
        if commit is not None and not re.fullmatch(r"[0-9a-fA-F]{" + str(length) + r"}", commit):
            raise ValueError("commit must be a full hexadecimal commit ID")
        commit = commit.lower() if commit else None
        for value in (ref, fetch_ref):
            if value is not None:
                validate_ref(value)
        if ref is not None and fetch_ref is not None:
            raise ValueError("fetch_ref is only used with a pinned commit")
        started = time.monotonic()
        lock: Lock | None = None
        lease: Lock | None = None
        entry: Path | None = None
        stage: Path | None = None
        git = self._git(deadline, cancel, local=local, credentials=credentials)
        try:
            root = self._root()
            key = self._key(source, local)
            entry = root / "entries" / key
            lock = Lock(root / "locks" / (key + ".lock"))
            lock.acquire(deadline, cancel)
            assert lock.fd is not None
            git.fds = [lock.fd]
            git.verify_version()
            emit_log(
                f"Repository {key[:12]} lock acquired in {time.monotonic() - started:.3f}s",
                source="repositories",
            )
            for partial in (root / "entries").glob(".partial-" + key + "-*"):
                self._remove(partial)
            if entry.exists():
                directory(entry)
                try:
                    if not (entry / "git" / "HEAD").is_file():
                        raise RepositoryError("cache_corrupt", "Repository cache is incomplete")
                    self._recover(entry, git)
                    if read_json(entry / "identity.json") != self._identity(source, local):
                        raise RepositoryError(
                            "cache_corrupt", "Repository cache identity is invalid"
                        )
                    code, _ = git.run(
                        "fsck",
                        "--full",
                        "--no-dangling",
                        "--no-reflogs",
                        cwd=entry / "git",
                        allow_failure=True,
                    )
                    if code:
                        raise RepositoryError(
                            "cache_corrupt", "Repository cache objects are damaged"
                        )
                except (ValueError, FileNotFoundError, RepositoryError) as error:
                    if isinstance(error, RepositoryError) and error.code != "cache_corrupt":
                        raise
                    if self._active(entry, git):
                        raise RepositoryError(
                            "cache_corrupt", "Damaged repository cache is still in use"
                        ) from None
                    quarantine = entry.with_name(".partial-" + key + "-" + uuid.uuid4().hex)
                    entry.rename(quarantine)
                    self._remove(quarantine)
                    emit_log(
                        f"Repository {key[:12]} damaged inactive cache removed",
                        source="repositories",
                        level="WARNING",
                    )
            self._space(root, git, exclude=key)
            cold = not entry.exists()
            if cold:
                stage = root / "entries" / (".partial-" + key + "-" + uuid.uuid4().hex)
                directory(stage)
                directory(stage / "owners")
                directory(stage / "workspaces")
                git.run(
                    "init",
                    "--bare",
                    "--template=",
                    "--object-format=" + self.object_format,
                    str(stage / "git"),
                )
                write_json(stage / "identity.json", self._identity(source, local))
            current = stage if stage is not None else entry
            bare = current / "git"
            hit = commit is not None and self._has_commit(bare, commit, git)
            if not hit:
                wanted = ref or fetch_ref or commit
                assert wanted is not None
                target = "refs/anyo/fetched/" + hashlib.sha256(wanted.encode()).hexdigest()
                emit_log(
                    f"Repository {key[:12]} {'cold acquisition' if cold else 'fetch'}",
                    source="repositories",
                )
                try:
                    git.run(
                        "fetch",
                        "--atomic",
                        "--no-tags",
                        "--no-recurse-submodules",
                        "--no-write-fetch-head",
                        "--",
                        source,
                        "+" + wanted + ":" + target,
                        cwd=bare,
                    )
                except RepositoryError as error:
                    if error.code != "git_failed":
                        raise
                    if fetch_ref is None:
                        raise RepositoryError(
                            "revision_unavailable",
                            "Cannot fetch the requested revision; check the remote and credentials",
                        ) from None
                if (
                    commit is not None
                    and not self._has_commit(bare, commit, git)
                    and fetch_ref is not None
                ):
                    # A moved PR ref may no longer include the admitted event's SHA.
                    git.run(
                        "fetch",
                        "--atomic",
                        "--no-tags",
                        "--no-recurse-submodules",
                        "--no-write-fetch-head",
                        "--",
                        source,
                        "+" + commit + ":refs/anyo/keep/" + commit,
                        cwd=bare,
                        allow_failure=True,
                    )
                if ref is not None:
                    _, commit = git.run("rev-parse", "--verify", target + "^{commit}", cwd=bare)
            assert commit is not None
            if not self._has_commit(bare, commit, git):
                raise RepositoryError(
                    "revision_unavailable", "The exact requested commit is unavailable"
                )
            git.run("update-ref", "refs/anyo/keep/" + commit, commit, cwd=bare)
            if stage is not None:
                self._space(
                    root, git, exclude=key, reserve_bytes=disk_size(stage, deadline, cancel)
                )
                stage.rename(entry)
                stage = None
                bare = entry / "git"
            token = uuid.uuid4().hex
            lease = Lock(entry / "owners" / (token + ".lock"))
            lease.acquire(deadline, cancel)
            assert lease.fd is not None
            git.fds.append(lease.fd)
            workspace = entry / "workspaces" / token
            git.run("worktree", "add", "--detach", "--lock", "--", str(workspace), commit, cwd=bare)
            _, actual = git.run("rev-parse", "HEAD", cwd=workspace)
            if actual != commit:
                raise RepositoryError(
                    "cache_corrupt", "Workspace does not match the requested commit"
                )
            warnings: list[str] = []
            _, modes = git.run("ls-tree", "-r", "--format=%(objectmode)", commit, cwd=bare)
            if "160000" in modes.splitlines():
                warnings.append("submodules_not_initialized")
            lfs, _ = git.run(
                "grep",
                "-q",
                "-F",
                "-e",
                "version https://git-lfs.github.com/spec/v1",
                commit,
                "--",
                cwd=bare,
                allow_failure=True,
            )
            if lfs == 0:
                warnings.append("lfs_pointers_only")
            self._space(root, git, exclude=key)
            os.utime(entry, None)
            emit_log(
                f"Repository {key[:12]} {'cache hit' if hit else 'prepared'} {commit} "
                f"in {time.monotonic() - started:.3f}s",
                source="repositories",
            )
            return _Owned(RepositoryCheckout(workspace, commit, hit, tuple(warnings)), entry, lease)
        except BaseException as error:
            if lease is not None:
                lease.close()
            if entry is not None and entry.exists() and lock is not None and lock.fd is not None:
                cleanup = self._git(_timeout(30), threading.Event())
                cleanup.fds = [lock.fd]
                try:
                    self._recover(entry, cleanup)
                    if (
                        isinstance(error, RepositoryError)
                        and error.code == "storage_full"
                        and not self._active(entry, cleanup)
                    ):
                        self._remove(entry)
                except (OSError, RepositoryError):
                    emit_log(
                        "Repository cleanup deferred to maintenance",
                        source="repositories",
                        level="WARNING",
                    )
            # If cleanup failed, its ownership record allows later lock-proven recovery.
            if stage is not None:
                self._remove(stage)
            if isinstance(error, OSError):
                raise storage_error(error) from None
            raise
        finally:
            if lock is not None:
                lock.close()

    def _has_commit(self, bare: Path, commit: str, git: Git) -> bool:
        code, result = git.run(
            "rev-parse", "--verify", commit + "^{commit}", cwd=bare, allow_failure=True
        )
        return code == 0 and result == commit

    def _release(self, owned: _Owned) -> None:
        lock: Lock | None = None
        try:
            git = self._git(_timeout(30), threading.Event())
            root = self._root()
            lock = Lock(root / "locks" / (owned.entry.name + ".lock"))
            lock.acquire(git.deadline, git.cancel)
            assert lock.fd is not None and owned.lease.fd is not None
            git.fds = [lock.fd, owned.lease.fd]
            self._remove_workspace(owned.entry, owned.checkout.path.name, git)
            os.utime(owned.entry, None)
            emit_log("Repository workspace released", source="repositories")
        except (RepositoryError, OSError):
            # Never hide a pipeline exception. The next acquisition/maintenance retries cleanup.
            emit_log(
                "Repository workspace cleanup deferred; run cache.maintain()",
                source="repositories",
                level="WARNING",
            )
        finally:
            owned.lease.close()
            if lock is not None:
                lock.close()

    @staticmethod
    def _remove(path: Path) -> None:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    def _remove_workspace(self, entry: Path, token: str, git: Git) -> None:
        if not re.fullmatch("[0-9a-f]{32}", token):
            raise RepositoryError("cache_corrupt", "Invalid workspace ownership record")
        workspace = entry / "workspaces" / token
        git.run(
            "worktree",
            "remove",
            "--force",
            "--force",
            "--",
            str(workspace),
            cwd=entry / "git",
            allow_failure=True,
        )
        self._remove(workspace)
        # A killed 'worktree add' can leave partial administration; this token is exclusively owned.
        self._remove(entry / "git" / "worktrees" / token)
        (entry / "owners" / (token + ".lock")).unlink(missing_ok=True)

    def _recover(self, entry: Path, git: Git) -> int:
        recovered = 0
        directory(entry / "owners")
        directory(entry / "workspaces")
        for path in (entry / "owners").glob("*.lock"):
            lease = Lock(path)
            if lease.acquire(git.deadline, git.cancel, wait=False):
                try:
                    self._remove_workspace(entry, path.stem, git)
                    recovered += 1
                finally:
                    lease.close()
        if not self._active(entry, git):
            # No owner or inherited Git descriptor remains: leftover Git lockfiles are abandoned.
            for path in (entry / "git").rglob("*.lock"):
                if path.is_file() and not path.is_symlink():
                    path.unlink()
        if recovered:
            emit_log(
                f"Recovered {recovered} abandoned repository workspaces", source="repositories"
            )
        return recovered

    def _active(self, entry: Path, git: Git) -> bool:
        for path in (entry / "owners").glob("*.lock"):
            lease = Lock(path)
            if not lease.acquire(git.deadline, git.cancel, wait=False):
                return True
            lease.close()
        return False

    def _space(
        self,
        root: Path,
        git: Git,
        *,
        exclude: str | None = None,
        expired: bool = False,
        reserve_bytes: int = 0,
    ) -> MaintenanceResult:
        removed = recovered = 0
        for partial in (root / "entries").glob(".partial-*"):
            match = re.fullmatch(r"\.partial-([0-9a-f]{64})-[0-9a-f]{32}", partial.name)
            if match is None or match[1] == exclude:
                continue
            lock = Lock(root / "locks" / (match[1] + ".lock"))
            if lock.acquire(git.deadline, git.cancel, wait=False):
                try:
                    self._remove(partial)
                finally:
                    lock.close()
        for candidate in (root / "entries").iterdir():
            if candidate.is_symlink():
                raise RepositoryError("cache_corrupt", "Managed cache entries cannot be symlinks")
        entries = sorted(
            (p for p in (root / "entries").iterdir() if re.fullmatch("[0-9a-f]{64}", p.name)),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
        )
        sizes = {p: disk_size(p, git.deadline, git.cancel) for p in entries}
        total = sum(sizes.values()) + reserve_bytes
        for entry in entries:
            low = shutil.disk_usage(root).free < self.min_free_bytes
            old = (
                expired
                and entry.exists()
                and time.time() - entry.stat().st_mtime >= self.idle_seconds
            )
            if entry.name == exclude:
                continue
            lock = Lock(root / "locks" / (entry.name + ".lock"))
            if not lock.acquire(git.deadline, git.cancel, wait=False):
                continue
            try:
                if not entry.exists():
                    total -= sizes[entry]
                    continue
                assert lock.fd is not None
                git.fds.append(lock.fd)
                recovered += self._recover(entry, git)
                if (low or total > self.max_bytes or old) and not self._active(entry, git):
                    self._remove(entry)
                    total -= sizes[entry]
                    removed += 1
            finally:
                if lock.fd in git.fds:
                    git.fds.remove(lock.fd)
                lock.close()
        # Recount after recovery; active workspaces may change concurrently.
        total = sum(disk_size(p, git.deadline, git.cancel) for p in entries if p.exists())
        if (
            total + reserve_bytes > self.max_bytes
            or shutil.disk_usage(root).free < self.min_free_bytes
        ):
            raise RepositoryError(
                "storage_full",
                "Cache limits reached; release workspaces or purge inactive entries",
            )
        return MaintenanceResult(removed, recovered, total)

    def maintain(self, *, timeout: float = 120) -> MaintenanceResult:
        """Recover dead owners and evict idle/over-budget entries; never remove active data."""
        git = self._git(_timeout(timeout), threading.Event())
        try:
            root = self._root()
            git.verify_version()
            return self._space(root, git, expired=True)
        except OSError as error:
            raise storage_error(error) from None

    def purge(
        self, *, url: str | None = None, local_path: str | Path | None = None, timeout: float = 30
    ) -> bool:
        """Purge one inactive repository, for example after access revocation."""
        source, local = source_url(url, local_path)
        git = self._git(_timeout(timeout), threading.Event())
        root = self._root()
        key = self._key(source, local)
        lock = Lock(root / "locks" / (key + ".lock"))
        lock.acquire(git.deadline, git.cancel)
        try:
            entry = root / "entries" / key
            if not entry.exists():
                return False
            assert lock.fd is not None
            git.fds = [lock.fd]
            self._recover(entry, git)
            if self._active(entry, git):
                raise RepositoryError(
                    "cache_busy", "Release active workspaces before purging this repository"
                )
            self._remove(entry)
            return True
        finally:
            lock.close()

    def export_seed(
        self,
        destination: str | Path,
        *,
        url: str | None = None,
        local_path: str | Path | None = None,
        timeout: float = 120,
    ) -> Path:
        """Create a new seed directory with a full bundle and identity/digest manifest."""
        source, local = source_url(url, local_path)
        git = self._git(_timeout(timeout), threading.Event())
        root = self._root()
        key = self._key(source, local)
        target = Path(destination).absolute()
        if target.exists():
            raise ValueError("Seed destination must not already exist")
        directory(target.parent)
        stage = target.with_name(".anyo-seed-" + uuid.uuid4().hex)
        lock = Lock(root / "locks" / (key + ".lock"))
        lock.acquire(git.deadline, git.cancel)
        try:
            assert lock.fd is not None
            git.fds = [lock.fd]
            entry = root / "entries" / key
            if not entry.exists():
                raise RepositoryError(
                    "cache_missing", "Acquire a repository before exporting a seed"
                )
            directory(stage)
            bundle = stage / "repository.bundle"
            git.run("bundle", "create", str(bundle), "--all", cwd=entry / "git")
            write_json(
                stage / "manifest.json",
                {
                    "identity": self._identity(source, local),
                    "sha256": digest(bundle, git.deadline, git.cancel),
                },
            )
            stage.rename(target)
            return target
        finally:
            self._remove(stage)
            lock.close()

    def restore_seed(
        self,
        seed: str | Path,
        *,
        url: str | None = None,
        local_path: str | Path | None = None,
        timeout: float = 120,
    ) -> None:
        """Initialize a cold cache from a trusted, self-contained seed."""
        source, local = source_url(url, local_path)
        git = self._git(_timeout(timeout), threading.Event(), local=True)
        root = self._root()
        key = self._key(source, local)
        entry = root / "entries" / key
        seed_path = Path(seed).absolute()
        try:
            manifest = read_json(seed_path / "manifest.json")
        except (OSError, ValueError, RepositoryError):
            raise RepositoryError("seed_invalid", "Seed manifest is missing or invalid") from None
        if not isinstance(manifest, dict) or manifest.get("identity") != self._identity(
            source, local
        ):
            raise RepositoryError(
                "seed_identity", "Seed does not match this repository and access scope"
            )
        lock = Lock(root / "locks" / (key + ".lock"))
        lock.acquire(git.deadline, git.cancel)
        stage = root / "entries" / (".partial-" + key + "-" + uuid.uuid4().hex)
        try:
            if entry.exists():
                raise RepositoryError("cache_exists", "Restore a seed only into a cold cache")
            assert lock.fd is not None
            git.fds = [lock.fd]
            git.verify_version()
            self._space(root, git, exclude=key)
            directory(stage)
            directory(stage / "owners")
            directory(stage / "workspaces")
            bundle = stage / "seed.bundle"
            if (seed_path / "repository.bundle").is_symlink():
                raise RepositoryError("seed_invalid", "Seed bundle must be a regular file")
            with (
                (seed_path / "repository.bundle").open("rb") as incoming,
                bundle.open("wb") as outgoing,
            ):
                while chunk := incoming.read(1024 * 1024):
                    check_deadline(git.deadline, git.cancel)
                    if outgoing.tell() + len(chunk) > self.max_bytes:
                        raise RepositoryError("storage_full", "Seed exceeds the cache size limit")
                    outgoing.write(chunk)
            if digest(bundle, git.deadline, git.cancel) != manifest.get("sha256"):
                raise RepositoryError("seed_invalid", "Seed bundle digest does not match")
            bare = stage / "git"
            git.run(
                "init", "--bare", "--template=", "--object-format=" + self.object_format, str(bare)
            )
            code, _ = git.run("bundle", "verify", str(bundle), cwd=bare, allow_failure=True)
            if code:
                raise RepositoryError(
                    "seed_invalid",
                    "Seed must be a valid self-contained bundle with no prerequisites",
                )
            git.run(
                "fetch",
                "--atomic",
                "--no-tags",
                "--no-write-fetch-head",
                "--",
                str(bundle),
                "+refs/*:refs/anyo/seed/*",
                cwd=bare,
            )
            git.run("fsck", "--full", "--no-dangling", cwd=bare)
            bundle.unlink()
            write_json(stage / "identity.json", self._identity(source, local))
            if disk_size(stage, git.deadline, git.cancel) > self.max_bytes:
                raise RepositoryError("storage_full", "Restored seed exceeds the cache size limit")
            self._space(
                root,
                git,
                exclude=key,
                reserve_bytes=disk_size(stage, git.deadline, git.cancel),
            )
            stage.rename(entry)
        except OSError as error:
            raise storage_error(error) from None
        finally:
            self._remove(stage)
            lock.close()
