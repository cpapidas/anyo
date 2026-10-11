"""Real local Git fixtures exercise persistent ownership and public repository APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from anyo.agents import Harness, Model, PromptClient, PromptResult, PromptSuccess
from anyo.observability import process_scope
from anyo.repositories import HTTPSCredentials, RepositoryCache, RepositoryError, SSHCredentials


def git(path, *args):
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        text=True,
        stderr=subprocess.PIPE,
        env={
            "PATH": os.environ["PATH"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
    ).strip()


def commit_file(origin, text="one\n"):
    (origin / "source.txt").write_text(text)
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "fixture")
    return git(origin, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path):
    path = tmp_path / "origin"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    commit_file(path)
    return path


@pytest.fixture
def cache(tmp_path):
    return RepositoryCache(tmp_path / "cache", min_free_bytes=0)


def entries(cache):
    return list(cache.directory.glob("*/entries/" + "[0-9a-f]" * 64))


def test_construction_is_inert_and_invalid_input_stays_inert(tmp_path):
    cache = RepositoryCache(tmp_path / "absent", executable="missing-git")
    assert not cache.directory.exists()
    for kwargs in [
        {},
        {"url": "https://example.invalid/r", "commit": "main"},
        {"url": "https://example.invalid/r", "ref": "-bad"},
        {"url": "https://example.invalid/r", "commit": "f" * 40, "ref": "refs/heads/main"},
        {
            "url": "https://example.invalid/r",
            "ref": "refs/heads/main",
            "fetch_ref": "refs/pull/1/head",
        },
    ]:
        with pytest.raises(ValueError):
            with cache.checkout(**kwargs):
                pytest.fail("Invalid input acquired a checkout")
    assert not cache.directory.exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.invalid/r",
        "https://user@example.invalid/r",
        "http://example.invalid/r",
        "file:///tmp/repository",
        "git://example.invalid/r",
        "ext::sh -c bad",
        "-option",
        "https://example.invalid/r?token=secret",
        "https://example.invalid/r#fragment",
        "https://example.invalid/r\nnext",
    ],
)
def test_rejects_unsafe_urls(cache, url):
    with pytest.raises(ValueError):
        with cache.checkout(url=url, commit="a" * 40):
            pass


@pytest.mark.parametrize(
    "ref",
    [
        "main",
        "refs/heads/../main",
        "refs/heads/main:other",
        "refs/heads/x.lock",
        "refs/heads/*",
        "refs//main",
        "refs/heads/a@{1}",
        "refs/anyo/keep/a",
        "refs/heads/.x",
    ],
)
def test_rejects_ref_injection(cache, origin, ref):
    with pytest.raises(ValueError):
        with cache.checkout(local_path=origin, ref=ref):
            pass


def test_exact_checkout_cleanup_and_offline_reuse(cache, origin):
    sha = git(origin, "rev-parse", "HEAD")
    with cache.checkout(local_path=origin, commit=sha) as first:
        assert first.path.is_absolute()
        assert first.commit_sha == sha
        assert not first.cache_hit
        assert git(first.path, "rev-parse", "HEAD") == sha
        (first.path / "source.txt").write_text("dirty")
    assert not first.path.exists()
    origin.rename(origin.with_name("offline"))
    with RepositoryCache(cache.directory).checkout(local_path=origin, commit=sha) as second:
        assert second.cache_hit
        assert second.path != first.path
        assert (second.path / "source.txt").read_text() == "one\n"
    assert len(entries(cache)) == 1


def test_named_ref_refreshes_and_retains_older_sha(cache, origin):
    with cache.checkout(local_path=origin, ref="refs/heads/main") as first:
        old = first.commit_sha
    new = commit_file(origin, "two\n")
    with cache.checkout(local_path=origin, ref="refs/heads/main") as second:
        assert second.commit_sha == new != old
        assert not second.cache_hit
    origin.rename(origin.with_name("offline"))
    with cache.checkout(local_path=origin, commit=old) as pinned:
        assert pinned.cache_hit
        assert (pinned.path / "source.txt").read_text() == "one\n"


def test_pr_ref_never_substitutes_advanced_head(cache, origin):
    old = git(origin, "rev-parse", "HEAD")
    new = commit_file(origin, "new head\n")
    git(origin, "update-ref", "refs/pull/17/head", new)
    with cache.checkout(local_path=origin, commit=old, fetch_ref="refs/pull/17/head") as checkout:
        assert checkout.commit_sha == old
    with pytest.raises(RepositoryError) as failure:
        with cache.checkout(local_path=origin, commit="f" * 40, fetch_ref="refs/pull/17/head"):
            pass
    assert failure.value.code == "revision_unavailable"


def test_missing_pr_ref_can_fetch_exact_commit(cache, origin):
    sha = git(origin, "rev-parse", "HEAD")
    with cache.checkout(local_path=origin, commit=sha, fetch_ref="refs/pull/999/head") as checkout:
        assert checkout.commit_sha == sha


def test_separate_scopes_do_not_reuse_private_data(cache, origin, tmp_path):
    sha = git(origin, "rev-parse", "HEAD")
    with cache.checkout(local_path=origin, commit=sha):
        pass
    origin.rename(origin.with_name("offline"))
    other = RepositoryCache(cache.directory, namespace="other")
    with pytest.raises(RepositoryError):
        with other.checkout(local_path=origin, commit=sha):
            pass


def test_concurrent_threads_have_distinct_isolated_workspaces(cache, origin):
    barrier = threading.Barrier(2)
    sha = git(origin, "rev-parse", "HEAD")

    def work(number):
        barrier.wait(timeout=10)
        with cache.checkout(local_path=origin, commit=sha) as checkout:
            (checkout.path / "source.txt").write_text(str(number))
            barrier.wait(timeout=10)
            assert (checkout.path / "source.txt").read_text() == str(number)
            assert cache.maintain().removed_entries == 0
            return checkout.path, checkout.cache_hit

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(work, (1, 2)))
    assert results[0][0] != results[1][0]
    assert sorted(result[1] for result in results) == [False, True]
    assert not any(path.exists() for path, _ in results)
    assert len(entries(cache)) == 1


CHILD = """
import json, sys
from anyo.repositories import RepositoryCache
sys.stdin.readline()
cache = RepositoryCache(sys.argv[1], executable=sys.argv[4])
with cache.checkout(local_path=sys.argv[2], commit=sys.argv[3]) as checkout:
    print(json.dumps({'path': str(checkout.path), 'hit': checkout.cache_hit}), flush=True)
    sys.stdin.readline()
"""


def start_child(cache, origin, sha, executable="git"):
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(cache.directory), str(origin), sha, executable],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def child_ready(child):
    assert select.select([child.stdout], [], [], 15)[0], "Child did not acquire repository"
    line = child.stdout.readline()
    assert line, child.stderr.read()
    return json.loads(line)


def test_process_concurrency_crash_recovery_and_live_owner_protection(cache, origin):
    sha = git(origin, "rev-parse", "HEAD")
    children = [start_child(cache, origin, sha) for _ in range(2)]
    try:
        for child in children:
            child.stdin.write("start\n")
            child.stdin.flush()
        first, second = [child_ready(child) for child in children]
        assert first["path"] != second["path"]
        assert sorted([first["hit"], second["hit"]]) == [False, True]
        Path(first["path"], "source.txt").unlink()
        assert Path(second["path"], "source.txt").read_text() == "one\n"
        children[0].kill()
        children[0].wait(timeout=5)
        maintenance = RepositoryCache(cache.directory, idle_seconds=0).maintain()
        assert maintenance.recovered_workspaces == 1
        assert maintenance.removed_entries == 0
        assert not Path(first["path"]).exists()
        assert Path(second["path"]).exists()
        with pytest.raises(RepositoryError) as failure:
            cache.purge(local_path=origin)
        assert failure.value.code == "cache_busy"
        children[1].stdin.write("finish\n")
        children[1].stdin.flush()
        assert children[1].wait(timeout=10) == 0
        origin.rename(origin.with_name("offline"))
        replacement = start_child(cache, origin, sha)
        children.append(replacement)
        replacement.stdin.write("start\nfinish\n")
        replacement.stdin.flush()
        assert child_ready(replacement)["hit"]
        assert replacement.wait(timeout=10) == 0
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


def test_async_release_on_callback_failure_and_cancellation(cache, origin):
    sha = git(origin, "rev-parse", "HEAD")

    async def scenario():
        started = asyncio.Event()
        paths = []

        async def work():
            async with cache.acheckout(local_path=origin, commit=sha) as checkout:
                paths.append(checkout.path)
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(work())
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not paths[0].exists()
        with pytest.raises(RuntimeError, match="consumer failure"):
            async with cache.acheckout(local_path=origin, commit=sha) as checkout:
                path = checkout.path
                raise RuntimeError("consumer failure")
        assert not path.exists()

    asyncio.run(scenario())


def slow_git(tmp_path):
    # A socket handshake blocks the real subprocess boundary deterministically.
    listener = socket.socket(socket.AF_INET)
    listener.bind(("127.0.0.1", 0))
    address = listener.getsockname()
    listener.listen()
    listener.settimeout(10)
    wrapper = tmp_path / "git-wrapper"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os,socket,sys\n"
        "if 'fetch' in sys.argv:\n s=socket.socket(socket.AF_INET)\n"
        f" s.connect({address!r});s.sendall(str(os.getpid()).encode());s.recv(1)\n"
        f"os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o700)
    return listener, wrapper


def test_cancellation_stops_owned_git_and_unlocks_partial_population(origin, tmp_path):
    listener, wrapper = slow_git(tmp_path)
    cache = RepositoryCache(tmp_path / "cache", executable=str(wrapper))
    sha = git(origin, "rev-parse", "HEAD")
    seen, stopped = [], []

    async def scenario():
        async def acquire():
            async with cache.acheckout(local_path=origin, commit=sha):
                pytest.fail("Blocked Git should not finish")

        with process_scope(seen.append, stopped.append):
            task = asyncio.create_task(acquire())
            connection, _ = await asyncio.to_thread(listener.accept)
            pid = int(await asyncio.to_thread(connection.recv, 64))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            connection.close()
        assert sorted(seen) == sorted(stopped)
        assert not entries(cache)
        with RepositoryCache(cache.directory).checkout(local_path=origin, commit=sha):
            pass

    try:
        asyncio.run(scenario())
    finally:
        listener.close()


def test_lock_timeout_does_not_steal_active_preparation(origin, tmp_path):
    listener, wrapper = slow_git(tmp_path)
    cache = RepositoryCache(tmp_path / "cache", executable=str(wrapper))
    sha = git(origin, "rev-parse", "HEAD")

    def acquire():
        with cache.checkout(local_path=origin, commit=sha) as checkout:
            return checkout.commit_sha

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(acquire)
            connection, _ = listener.accept()
            connection.recv(64)
            try:
                with pytest.raises(RepositoryError) as failure:
                    with cache.checkout(local_path=origin, commit=sha, timeout=0.1):
                        pass
                assert failure.value.code == "lock_timeout"
            finally:
                connection.sendall(b"x")
                connection.close()
            assert future.result(timeout=10) == sha
    finally:
        listener.close()


def test_seed_restore_offline_then_incremental_update(cache, origin, tmp_path):
    old = git(origin, "rev-parse", "HEAD")
    with cache.checkout(local_path=origin, commit=old):
        pass
    seed = cache.export_seed(tmp_path / "seed", local_path=origin)
    other = RepositoryCache(tmp_path / "restored")
    other.restore_seed(seed, local_path=origin)
    moved = origin.with_name("offline")
    origin.rename(moved)
    with other.checkout(local_path=origin, commit=old) as checkout:
        assert checkout.cache_hit
    moved.rename(origin)
    new = commit_file(origin, "new\n")
    with other.checkout(local_path=origin, commit=new) as checkout:
        assert checkout.commit_sha == new
    with other.checkout(local_path=origin, commit=old) as checkout:
        assert checkout.cache_hit
    with pytest.raises(RepositoryError) as failure:
        other.restore_seed(seed, local_path=origin)
    assert failure.value.code == "cache_exists"


@pytest.mark.parametrize("damage", ["identity", "digest", "prerequisites"])
def test_invalid_seed_never_publishes_cache(cache, origin, tmp_path, damage):
    first = git(origin, "rev-parse", "HEAD")
    commit_file(origin, "two\n")
    with cache.checkout(local_path=origin, ref="refs/heads/main"):
        pass
    seed = cache.export_seed(tmp_path / "seed", local_path=origin)
    manifest = json.loads((seed / "manifest.json").read_text())
    if damage == "identity":
        manifest["identity"]["namespace"] = "another-scope"
    elif damage == "digest":
        (seed / "repository.bundle").write_bytes(b"damaged")
    else:
        (seed / "repository.bundle").unlink()
        git(origin, "bundle", "create", str(seed / "repository.bundle"), f"{first}..HEAD")
        manifest["sha256"] = hashlib.sha256((seed / "repository.bundle").read_bytes()).hexdigest()
    (seed / "manifest.json").write_text(json.dumps(manifest))
    other = RepositoryCache(tmp_path / "restored")
    with pytest.raises(RepositoryError) as failure:
        other.restore_seed(seed, local_path=origin)
    assert failure.value.code in {"seed_identity", "seed_invalid"}
    assert not entries(other)


def test_inactive_corruption_rebuilds_but_active_cache_is_not_replaced(cache, origin):
    sha = git(origin, "rev-parse", "HEAD")
    with cache.checkout(local_path=origin, commit=sha) as checkout:
        bare = entries(cache)[0] / "git"
        pack = next((bare / "objects" / "pack").glob("*.pack"))
        pack.chmod(0o600)
        pack.write_bytes(b"corrupt")
        with pytest.raises(RepositoryError) as failure:
            with cache.checkout(local_path=origin, commit=sha):
                pass
        assert failure.value.code == "cache_corrupt"
        assert checkout.path.exists()
    with cache.checkout(local_path=origin, commit=sha) as checkout:
        assert not checkout.cache_hit
        assert (checkout.path / "source.txt").read_text() == "one\n"


def test_maintenance_retention_space_limit_and_purge(cache, origin):
    sha = git(origin, "rev-parse", "HEAD")
    with cache.checkout(local_path=origin, commit=sha):
        too_small = RepositoryCache(cache.directory, max_bytes=1, min_free_bytes=0)
        with pytest.raises(RepositoryError) as failure:
            too_small.maintain()
        assert failure.value.code == "storage_full"
        assert entries(cache)
    assert too_small.maintain().removed_entries == 1
    with cache.checkout(local_path=origin, commit=sha):
        assert RepositoryCache(cache.directory, idle_seconds=0).maintain().removed_entries == 0
    assert RepositoryCache(cache.directory, idle_seconds=0).maintain().removed_entries == 1
    with cache.checkout(local_path=origin, commit=sha):
        pass
    assert cache.purge(local_path=origin)
    assert not cache.purge(local_path=origin)


def test_ambient_hooks_filters_and_credentials_are_not_executed(
    cache, origin, tmp_path, monkeypatch
):
    marker = tmp_path / "should-not-exist"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o700)
    config = tmp_path / "gitconfig"
    config.write_text(
        f'[core]\n hooksPath = {hooks}\n[filter "evil"]\n smudge = touch {marker}\n'
        f" required = true\n[credential]\n helper = !touch {marker}\n"
    )
    (origin / ".gitattributes").write_text("source.txt filter=evil\n")
    sha = commit_file(origin)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))
    with cache.checkout(local_path=origin, commit=sha) as checkout:
        assert (checkout.path / "source.txt").read_text() == "one\n"
    assert not marker.exists()


def test_submodule_and_lfs_limits_are_visible(cache, origin):
    sha = git(origin, "rev-parse", "HEAD")
    git(origin, "update-index", "--add", "--cacheinfo", f"160000,{sha},nested")
    (origin / "large.bin").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 999\n"
    )
    git(origin, "add", "large.bin")
    git(origin, "commit", "-qm", "external content")
    with cache.checkout(local_path=origin, ref="refs/heads/main") as checkout:
        assert checkout.warnings == ("submodules_not_initialized", "lfs_pointers_only")
        assert not list((checkout.path / "nested").iterdir())
        assert "git-lfs" in (checkout.path / "large.bin").read_text()


def test_prompt_client_uses_workspace_while_its_context_owns_it(cache, origin, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Read source.txt")
    sha = git(origin, "rev-parse", "HEAD")

    class Backend:
        async def invoke(self, request, emit):
            assert request.workspace.exists()
            return PromptResult(
                request.run_id,
                success=PromptSuccess((request.workspace / "source.txt").read_text()),
            )

        async def close(self):
            pass

    harness = Harness("fixture", (Model("test/model", "test"),), (), Backend)
    with cache.checkout(local_path=origin, commit=sha) as checkout:
        with PromptClient(harness=harness) as client:
            result = client.inject_prompt(prompt, workspace=checkout.path)
        assert result.success.data == "one\n"
        assert checkout.path.exists()
    assert not checkout.path.exists()


def test_authentication_configuration_validation(cache):
    credentials = HTTPSCredentials("username-sentinel", "password-sentinel")
    assert "sentinel" not in repr(credentials)
    with pytest.raises(ValueError):
        HTTPSCredentials("name", "bad\nvalue")
    with pytest.raises(ValueError):
        with cache.checkout(url="ssh://example.invalid/repo", commit="a" * 40):
            pass
    with pytest.raises(ValueError):
        with cache.checkout(
            url="https://example.invalid/repo",
            commit="a" * 40,
            credentials=SSHCredentials(Path("known_hosts")),
        ):
            pass


def test_missing_git_and_timeouts_are_actionable(origin, tmp_path):
    with pytest.raises(RepositoryError) as failure:
        with RepositoryCache(tmp_path / "cache", executable="anyo-no-such-git").checkout(
            local_path=origin, ref="refs/heads/main"
        ):
            pass
    assert failure.value.code == "git_missing"
    for timeout in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            with RepositoryCache(tmp_path / "cache").checkout(
                local_path=origin, ref="refs/heads/main", timeout=timeout
            ):
                pass


def test_git_child_retains_mutation_lock_after_worker_death(origin, tmp_path):
    listener, wrapper = slow_git(tmp_path)
    cache = RepositoryCache(tmp_path / "cache")
    sha = git(origin, "rev-parse", "HEAD")
    child = start_child(cache, origin, sha, str(wrapper))
    connection = None
    try:
        child.stdin.write("start\n")
        child.stdin.flush()
        connection, _ = listener.accept()
        connection.recv(64)
        child.kill()
        child.wait(timeout=5)
        with pytest.raises(RepositoryError) as failure:
            with cache.checkout(local_path=origin, commit=sha, timeout=0.1):
                pass
        assert failure.value.code == "lock_timeout"
        connection.sendall(b"x")
        connection.close()
        connection = None
        with cache.checkout(local_path=origin, commit=sha, timeout=10) as checkout:
            assert (checkout.path / "source.txt").read_text() == "one\n"
        assert not list(cache.directory.glob("*/entries/.partial-*"))
    finally:
        if connection is not None:
            connection.sendall(b"x")
            connection.close()
        listener.close()
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_git_timeout_reaps_process_before_return(origin, tmp_path):
    listener, wrapper = slow_git(tmp_path)
    cache = RepositoryCache(tmp_path / "cache", executable=str(wrapper))
    sha = git(origin, "rev-parse", "HEAD")

    def acquire():
        with cache.checkout(local_path=origin, commit=sha, timeout=1):
            pytest.fail("Timed-out preparation cannot return a checkout")

    try:
        with ThreadPoolExecutor(1) as pool:
            task = pool.submit(acquire)
            connection, _ = listener.accept()
            pid = int(connection.recv(64))
            with pytest.raises(RepositoryError) as failure:
                task.result(timeout=5)
            assert failure.value.code == "timeout"
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            connection.close()
        assert not entries(cache)
    finally:
        listener.close()


def test_private_https_credentials_work_without_leaking(cache, origin, tmp_path, monkeypatch):
    import base64
    import functools
    import http.server
    import ssl

    sha = git(origin, "rev-parse", "HEAD")
    public = tmp_path / "www"
    public.mkdir()
    git(public, "clone", "--bare", str(origin), "repository.git")
    git(public / "repository.git", "update-server-info")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    username, password = "test-user-sentinel", "test-password-sentinel"
    monkeypatch.setenv("PRIVATE_REPOSITORY_TOKEN", password)
    authorized = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    seen = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization") != authorized:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="repository"')
                self.end_headers()
                return
            seen.append(self.path)
            super().do_GET()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(public))
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    args_seen = []
    original_popen = subprocess.Popen

    def audited_popen(args, **kwargs):
        args_seen.append(args)
        return original_popen(args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", audited_popen)
    url = f"https://127.0.0.1:{server.server_port}/repository.git"
    try:
        with cache.checkout(
            url=url, commit=sha, credentials=HTTPSCredentials(username, password, ca_file=cert)
        ) as checkout:
            assert (checkout.path / "source.txt").read_text() == "one\n"
            from anyo.observability import model_environment

            assert "PRIVATE_REPOSITORY_TOKEN" not in model_environment(dict(os.environ))
            assert os.environ["PRIVATE_REPOSITORY_TOKEN"] == password
        assert model_environment(dict(os.environ))["PRIVATE_REPOSITORY_TOKEN"] == password
        assert seen
        assert username not in repr(args_seen) and password not in repr(args_seen)
        seed = cache.export_seed(tmp_path / "private-seed", url=url)
        for root in (cache.directory, seed):
            for path in root.rglob("*"):
                if path.is_file():
                    assert password.encode() not in path.read_bytes()
                    assert username.encode() not in path.read_bytes()
        assert "ANYO_GIT_PASSWORD" not in os.environ
        cache.purge(url=url)
        with pytest.raises(RepositoryError) as failure:
            with cache.checkout(
                url=url, commit=sha, credentials=HTTPSCredentials(username, "expired", ca_file=cert)
            ):
                pass
        assert failure.value.code == "authentication_failed"
        assert "expired" not in str(failure.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def repository_pipeline_module(tmp_path, monkeypatch):
    import importlib
    import textwrap
    import uuid

    name = "anyo_repository_fixture_" + uuid.uuid4().hex
    (tmp_path / (name + ".py")).write_text(
        textwrap.dedent("""
        import json
        import socket
        from pathlib import Path
        from anyo.repositories import RepositoryCache

        def pipeline(event):
            data = event.payload
            cache = RepositoryCache(data['cache'], executable=data.get('executable', 'git'))
            with cache.checkout(local_path=data['origin'], commit=data['sha']) as checkout:
                Path(data['output']).write_text(json.dumps({
                    'path': str(checkout.path), 'sha': checkout.commit_sha,
                    'text': (checkout.path / 'source.txt').read_text(),
                }))
                if data.get('port'):
                    with socket.create_connection(('127.0.0.1', data['port'])) as connection:
                        connection.sendall(b'ready')
                        connection.recv(1)

        async def asynchronous(event):
            data = event.payload
            async with RepositoryCache(data['cache']).acheckout(
                local_path=data['origin'], commit=data['sha'],
            ) as checkout:
                Path(data['output']).write_text(json.dumps({
                    'path': str(checkout.path), 'sha': checkout.commit_sha,
                    'text': (checkout.path / 'source.txt').read_text(),
                }))
    """)
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return importlib.import_module(name)


async def queue_repository_run(queue, module, payload, mode, callback):
    from anyo.execution._codec import EventCodecRegistry
    from anyo.execution._worker import DurableDispatcher
    from anyo.hooks import Hook, HookEvent

    hook = Hook("local", "test", "ready", {}, getattr(module, callback), stable_key="repo")
    dispatcher = DurableDispatcher(queue, [hook], EventCodecRegistry(), execution_mode=mode)
    await dispatcher.start()
    try:
        await dispatcher.submit([(hook, HookEvent("test", "ready", "repository", payload=payload))])
        async with asyncio.timeout(20):
            while True:
                records = await queue.inspect()
                if records and records[0].status in {"successful", "failed", "interrupted"}:
                    assert records[0].status == "successful", records[0]
                    break
                await asyncio.sleep(0.01)
    finally:
        await dispatcher.close()
    report = json.loads(Path(payload["output"]).read_text())
    assert report["sha"] == payload["sha"] and report["text"] == "one\n"
    assert not Path(report["path"]).exists()


@pytest.mark.parametrize(
    "mode,callback",
    [
        ("thread", "pipeline"),
        ("thread", "asynchronous"),
        ("process", "pipeline"),
        ("process", "asynchronous"),
    ],
)
def test_real_sqlite_workers_own_repository_workspaces(
    cache, origin, tmp_path, repository_pipeline_module, mode, callback
):
    from anyo.execution import QueueSettings
    from anyo.integrations.sqlite import SqliteQueue

    payload = {
        "cache": str(cache.directory),
        "origin": str(origin),
        "sha": git(origin, "rev-parse", "HEAD"),
        "output": str(tmp_path / "result.json"),
    }
    queue = SqliteQueue(f"sqlite:///{tmp_path / 'queue.db'}", "repositories", QueueSettings())
    asyncio.run(queue_repository_run(queue, repository_pipeline_module, payload, mode, callback))


def test_force_stopping_process_releases_checkout_lease(
    cache, origin, tmp_path, repository_pipeline_module
):
    from anyo.execution._process import IsolatedRun
    from anyo.hooks import HookEvent

    listener = socket.socket(socket.AF_INET)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(10)
    payload = {
        "cache": str(cache.directory),
        "origin": str(origin),
        "sha": git(origin, "rev-parse", "HEAD"),
        "output": str(tmp_path / "result.json"),
        "port": listener.getsockname()[1],
    }

    async def scenario():
        runner = IsolatedRun(
            repository_pipeline_module.pipeline,
            HookEvent("test", "ready", "id", payload=payload),
            None,
            worker_id="worker",
            run_id="run",
            attempt=1,
        )
        task = asyncio.create_task(runner.run())
        connection, _ = await asyncio.to_thread(listener.accept)
        assert await asyncio.to_thread(connection.recv, 64) == b"ready"
        runner.cancel(force=True)
        result = await asyncio.wait_for(task, 10)
        assert result.outcome == "cancelled"
        connection.close()
        report = json.loads((tmp_path / "result.json").read_text())
        assert cache.maintain().recovered_workspaces == 1
        assert not Path(report["path"]).exists()
        with cache.checkout(local_path=origin, commit=payload["sha"]) as checkout:
            assert checkout.cache_hit

    try:
        asyncio.run(scenario())
    finally:
        listener.close()


@pytest.mark.live
@pytest.mark.parametrize("mode", ["thread", "process"])
def test_real_redis_workers_own_repository_workspaces(
    cache, origin, tmp_path, repository_pipeline_module, mode
):
    from uuid import uuid4

    from anyo.execution import QueueSettings
    from anyo.integrations.redis import RedisQueue

    endpoint = os.environ.get("ANYO_TEST_REDIS_URL")
    if not endpoint:
        pytest.skip("Set ANYO_TEST_REDIS_URL and select -m live")
    import redis.asyncio

    namespace = "repository-test-" + uuid4().hex
    payload = {
        "cache": str(cache.directory),
        "origin": str(origin),
        "sha": git(origin, "rev-parse", "HEAD"),
        "output": str(tmp_path / "result.json"),
    }

    async def scenario():
        queue = RedisQueue(endpoint, namespace, QueueSettings())
        admin = redis.asyncio.Redis.from_url(endpoint)
        try:
            await queue_repository_run(queue, repository_pipeline_module, payload, mode, "pipeline")
        finally:
            prefix = "anyo:queue:{" + hashlib.sha256(namespace.encode()).hexdigest() + "}:*"
            keys = [key async for key in admin.scan_iter(match=prefix)]
            if keys:
                await admin.delete(*keys)
            await admin.aclose()

    asyncio.run(scenario())


def test_full_sha256_commit_and_cache_identity(tmp_path):
    origin = tmp_path / "sha256-origin"
    origin.mkdir()
    git(origin, "init", "-q", "--object-format=sha256")
    sha = commit_file(origin)
    cache = RepositoryCache(tmp_path / "cache", object_format="sha256")
    with cache.checkout(local_path=origin, commit=sha) as checkout:
        assert checkout.commit_sha == sha and len(sha) == 64
    with pytest.raises(ValueError):
        with RepositoryCache(cache.directory).checkout(local_path=origin, commit=sha):
            pass


def test_preparation_budget_failure_removes_inactive_entry(origin, tmp_path):
    cache = RepositoryCache(tmp_path / "cache", max_bytes=1, min_free_bytes=0)
    with pytest.raises(RepositoryError) as failure:
        with cache.checkout(local_path=origin, ref="refs/heads/main"):
            pass
    assert failure.value.code == "storage_full"
    assert not entries(cache)
    assert not list(cache.directory.glob("*/entries/.partial-*"))


def test_missing_bare_repository_repairs_only_inactive_entry(cache, origin):
    with cache.checkout(local_path=origin, ref="refs/heads/main") as checkout:
        sha = checkout.commit_sha
    shutil.rmtree(entries(cache)[0] / "git")
    with cache.checkout(local_path=origin, commit=sha) as checkout:
        assert not checkout.cache_hit
        assert checkout.commit_sha == sha


def test_ssh_has_no_ambient_identity_fallback_for_missing_key(tmp_path):
    import shlex
    import time

    from anyo.repositories._git import Git

    if shutil.which("ssh") is None:
        pytest.skip("OpenSSH client is required for SSH configuration verification")
    credentials = SSHCredentials(
        tmp_path / "known hosts", identity_file=tmp_path / "missing private key"
    )
    runner = Git("git", time.monotonic() + 5, threading.Event(), credentials=credentials)
    result = subprocess.run(
        [*shlex.split(runner.env["GIT_SSH_COMMAND"]), "-G", "example.invalid"],
        env=runner.env,
        check=True,
        capture_output=True,
        text=True,
    )
    identities = [line for line in result.stdout.splitlines() if line.startswith("identityfile ")]
    assert identities == ["identityfile none"]
    assert (
        "stricthostkeychecking true" in result.stdout
        or "stricthostkeychecking yes" in result.stdout
    )
