"""Local filesystem coordination shared by threads, processes, and containers."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from ._git import RepositoryError, check_deadline


def directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RepositoryError("storage_unavailable", "Managed repository paths must be directories")


class Lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def acquire(self, deadline: float, cancel: threading.Event, *, wait: bool = True) -> bool:
        import fcntl

        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            while True:
                check_deadline(deadline, cancel)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fd = fd
                    return True
                except BlockingIOError:
                    if not wait:
                        os.close(fd)
                        return False
                    if cancel.wait(min(0.02, max(0, deadline - time.monotonic()))):
                        check_deadline(deadline, cancel)
                    if time.monotonic() >= deadline:
                        raise RepositoryError(
                            "lock_timeout", "Repository cache is busy; acquisition timed out"
                        )
        except BaseException:
            os.close(fd)
            raise

    def close(self) -> None:
        if self.fd is not None:
            # Do not LOCK_UN: a surviving Git child must keep this lease until it exits.
            os.close(self.fd)
            self.fd = None


def write_json(path: Path, data: object) -> None:
    # Callers hold the entry mutex; replacement leaves either a full old or new record.
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path: Path) -> object:
    if path.is_symlink() or path.stat().st_size > 16384:
        raise RepositoryError("cache_corrupt", "Repository metadata is invalid")
    result: object = json.loads(path.read_text())
    return result


def disk_size(path: Path, deadline: float, cancel: threading.Event) -> int:
    total = 0
    for current, directories, files in os.walk(path, followlinks=False):
        check_deadline(deadline, cancel)
        directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
        for name in files:
            try:
                total += (Path(current) / name).lstat().st_size
            except FileNotFoundError:
                pass
    return total


def digest(path: Path, deadline: float, cancel: threading.Event) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            check_deadline(deadline, cancel)
            result.update(chunk)
    return result.hexdigest()
