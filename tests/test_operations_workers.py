"""Operations controls against owned processes and a real shared SQLite queue."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import textwrap
import uuid

import pytest

from anyo.execution._codec import EventCodecRegistry
from anyo.execution._contracts import QueueSettings, QueueUnavailable
from anyo.execution._operations import OperationsSettings
from anyo.execution._worker import DurableDispatcher
from anyo.hooks import Hook, HookEvent, RetryPolicy
from anyo.integrations.sqlite import SqliteQueue


def run(coroutine):
    async def bounded():
        await asyncio.wait_for(coroutine, 20)

    asyncio.run(bounded())


async def until(predicate):
    async with asyncio.timeout(10):
        while not (value := await predicate()):
            await asyncio.sleep(0.01)
        return value


async def file_exists(path):
    return path.exists()


async def state(queue, identifier, expected):
    return next(
        (
            record
            for record in await queue.inspect()
            if record.id == identifier and record.status == expected
        ),
        None,
    )


def fixture_module(tmp_path, monkeypatch):
    name = f"anyo_control_fixture_{uuid.uuid4().hex}"
    module_path = tmp_path / f"{name}.py"
    module_path.write_text(
        textwrap.dedent("""
        import asyncio
        import logging
        import os
        import subprocess
        import sys
        import time
        from pathlib import Path
        from anyo.execution._codec import EventCodecRegistry
        from anyo.execution._contracts import QueueSettings
        from anyo.execution._operations import OperationsSettings
        from anyo.execution._worker import DurableDispatcher
        from anyo.hooks import Hook, RetryPolicy
        from anyo.integrations.sqlite import SqliteQueue

        def pipeline(event):
            target = Path(event.payload['path'])
            print('stdout ' + event.delivery_id, flush=True)
            print('stderr ' + event.delivery_id, file=sys.stderr, flush=True)
            logging.warning('logging ' + event.delivery_id)
            if event.payload.get('stuck'):
                child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
                target.with_suffix('.child').write_text(str(child.pid))
                target.write_text(str(os.getpid()))
                while True:
                    time.sleep(1)
            target.write_text('completed')

        async def asynchronous(event):
            target = Path(event.payload['path'])
            target.write_text('started')
            try:
                await asyncio.Event().wait()
            finally:
                target.with_suffix('.cleanup').write_text('cancelled')

        def runner(url, mode='process', run_workers=True, namespace='controls'):
            hook = Hook('local', 'test', 'ready', {}, pipeline,
                        stable_key='pipeline', retry=RetryPolicy(3, 0, True))
            if url.startswith('redis://'):
                from anyo.integrations.redis import RedisQueue
                queue = RedisQueue(url, namespace, QueueSettings())
            else:
                queue = SqliteQueue(url, namespace, QueueSettings())
            dispatcher = DurableDispatcher(queue, [hook], EventCodecRegistry(), workers=2,
                run_workers=run_workers, poll_interval=0.02, execution_mode=mode,
                operations=OperationsSettings())
            return dispatcher, queue, hook
    """)
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return importlib.import_module(name)


def test_process_configuration_rejects_closure_before_opening_queue(tmp_path):
    async def scenario():
        queue = SqliteQueue(f"sqlite:///{tmp_path / 'queue.db'}", "validation", QueueSettings())

        def callback(event):
            return None

        hook = Hook("local", "test", "ready", {}, callback, stable_key="hook")
        dispatcher = DurableDispatcher(
            queue, [hook], EventCodecRegistry(), execution_mode="process"
        )
        with pytest.raises(ValueError, match="top-level pipeline"):
            await dispatcher.start()
        assert not (tmp_path / "queue.db").exists()

    run(scenario())


def test_thread_cancel_is_pending_until_sync_callback_finishes_and_drain_keeps_backlog(tmp_path):
    async def scenario():
        import threading

        queue = SqliteQueue(f"sqlite:///{tmp_path / 'queue.db'}", "threads", QueueSettings())
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        called = []

        def callback(event):
            called.append(event.delivery_id)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)

        hook = Hook(
            "local", "test", "ready", {}, callback, stable_key="hook", retry=RetryPolicy(3, 0, True)
        )
        dispatcher = DurableDispatcher(
            queue,
            [hook],
            EventCodecRegistry(),
            workers=1,
            operations=OperationsSettings(),
            poll_interval=0.01,
        )
        await dispatcher.start()
        try:
            await dispatcher.submit([(hook, HookEvent("test", "ready", "one"))])
            await entered.wait()
            identifier = (await queue.inspect())[0].id
            assert await queue.cancel(identifier)
            # Wait until the supervisor has observed and attempted cancellation.
            await until(lambda: seen_command(dispatcher, identifier))
            record = (await queue.inspect())[0]
            assert record.status == "running" and record.control == "cancel"
            assert await queue.worker_command(dispatcher.worker_id)
            await until(lambda: worker_state(queue, "draining"))
            await dispatcher.submit([(hook, HookEvent("test", "ready", "two"))])
            release.set()
            await dispatcher.wait_stopped()
            assert await state(queue, identifier, "cancelled")
            assert called == ["one"]
            assert any(record.status == "queued" for record in await queue.inspect())
        finally:
            release.set()
            await dispatcher.close()

    run(scenario())


async def seen_command(dispatcher, identifier):
    attempt = dispatcher._active.get(identifier)
    return attempt is not None and attempt.command == "cancel"


async def worker_state(queue, expected):
    return any(worker.state == expected for worker in await queue.workers())


def test_control_storage_failure_terminates_owned_process_and_closes_resources(
    tmp_path, monkeypatch
):
    module = fixture_module(tmp_path, monkeypatch)

    async def scenario():
        dispatcher, queue, hook = module.runner(f"sqlite:///{tmp_path / 'queue.db'}")
        await dispatcher.start()
        marker = tmp_path / "stuck"
        try:
            await dispatcher.submit(
                [
                    (
                        hook,
                        HookEvent(
                            "test",
                            "ready",
                            "stuck",
                            payload={
                                "path": str(marker),
                                "stuck": True,
                            },
                        ),
                    )
                ]
            )
            await until(lambda: file_exists(marker))

            async def unavailable(claim):
                raise QueueUnavailable("Storage unavailable")

            monkeypatch.setattr(queue, "control", unavailable)
            with pytest.raises(QueueUnavailable):
                await dispatcher.wait_failure()
            # Final fence inspection may fail too, but cleanup still owns the
            # child and storage lease recovery determines the uncertain result.
            with pytest.raises(QueueUnavailable):
                await dispatcher.close()
            assert dispatcher._closed
            assert not process_alive(int(marker.read_text()))
            await until(lambda: process_gone(int(marker.with_suffix(".child").read_text())))
        finally:
            if not dispatcher._closed:
                for attempt in tuple(dispatcher._active.values()):
                    if attempt.process:
                        attempt.process.cancel(force=True)
                try:
                    await dispatcher.close()
                except QueueUnavailable:
                    pass

    run(scenario())


def test_spawn_async_cancel_runs_cleanup_without_stopping_sibling(tmp_path, monkeypatch):
    module = fixture_module(tmp_path, monkeypatch)

    async def scenario():
        queue = SqliteQueue(f"sqlite:///{tmp_path / 'queue.db'}", "async", QueueSettings())
        hook = Hook("local", "test", "ready", {}, module.asynchronous, stable_key="async")
        dispatcher = DurableDispatcher(
            queue,
            [hook],
            EventCodecRegistry(),
            workers=2,
            execution_mode="process",
            operations=OperationsSettings(),
            poll_interval=0.01,
        )
        await dispatcher.start()
        try:
            first, second = tmp_path / "first", tmp_path / "second"
            await dispatcher.submit(
                [
                    (hook, HookEvent("test", "ready", "first", payload={"path": str(first)})),
                    (hook, HookEvent("test", "ready", "second", payload={"path": str(second)})),
                ]
            )
            await until(lambda: file_exists(first))
            await until(lambda: file_exists(second))
            records = await queue.inspect()
            identifier = next(
                record.id
                for record in records
                if EventCodecRegistry()
                .decode(dispatcher._active[record.id].claim.job.event)
                .delivery_id
                == "first"
            )
            assert await queue.cancel(identifier)
            await until(lambda: state(queue, identifier, "cancelled"))
            assert first.with_suffix(".cleanup").read_text() == "cancelled"
            assert not second.with_suffix(".cleanup").exists()
            assert await queue.worker_command(dispatcher.worker_id, force=True)
            await dispatcher.wait_stopped()
            assert all(record.status == "cancelled" for record in await queue.inspect())
        finally:
            for attempt in tuple(dispatcher._active.values()):
                if attempt.process:
                    attempt.process.cancel(force=True)
            await dispatcher.close()

    run(scenario())


@pytest.mark.parametrize("backend", ["sqlite", pytest.param("redis", marks=pytest.mark.live)])
def test_controls_cross_process_force_stuck_run_and_preserve_receiver(
    tmp_path, monkeypatch, backend
):
    module = fixture_module(tmp_path, monkeypatch)

    async def scenario():
        url = (
            os.environ.get("ANYO_TEST_REDIS_URL", "")
            if backend == "redis"
            else f"sqlite:///{tmp_path / 'queue.db'}"
        )
        if not url:
            pytest.skip("Set ANYO_TEST_REDIS_URL to an explicitly selected test Redis")
        namespace = "anyo-process-" + uuid.uuid4().hex
        ready = tmp_path / "worker.ready"
        script = textwrap.dedent(f"""
            import asyncio
            from pathlib import Path
            from {module.__name__} import runner
            async def main():
                dispatcher, _, _ = runner({url!r}, namespace={namespace!r})
                await dispatcher.start()
                Path({str(ready)!r}).write_text(dispatcher.worker_id)
                try:
                    await dispatcher.wait_stopped()
                finally:
                    await dispatcher.close()
            asyncio.run(main())
        """)
        env = dict(os.environ, PYTHONPATH=str(tmp_path))
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        dispatcher, queue, hook = module.runner(url, run_workers=False, namespace=namespace)
        await dispatcher.start()
        try:
            await until(lambda: file_exists(ready))
            stuck, sibling = tmp_path / "stuck", tmp_path / "sibling"
            await dispatcher.submit(
                [
                    (
                        hook,
                        HookEvent(
                            "test", "ready", "stuck", payload={"path": str(stuck), "stuck": True}
                        ),
                    ),
                    (hook, HookEvent("test", "ready", "sibling", payload={"path": str(sibling)})),
                ]
            )
            await until(lambda: file_exists(stuck))
            await until(lambda: file_exists(sibling))
            records = await queue.inspect()
            identifier = next(record.id for record in records if record.status == "running")
            await until(lambda: logged(queue, identifier))
            assert await queue.cancel(identifier, force=True)
            await until(lambda: state(queue, identifier, "cancelled"))
            await dispatcher.wait_idle()
            assert sibling.read_text() == "completed"
            assert any(record.status == "successful" for record in await queue.inspect())
            assert not process_alive(int(stuck.read_text()))
            # Owned ordinary subprocesses inherit the isolated run group.
            await until(lambda: process_gone(int(stuck.with_suffix(".child").read_text())))
            assert (
                await dispatcher.submit(
                    [
                        (
                            hook,
                            HookEvent(
                                "test", "ready", "next", payload={"path": str(tmp_path / "next")}
                            ),
                        )
                    ]
                )
                == 1
            )
            await dispatcher.wait_idle()
            await until(lambda: logged(queue, identifier))
            entries = (await queue.logs(run_id=identifier)).entries
            assert any("stdout stuck" in entry.message for entry in entries)
            assert any("stderr stuck" in entry.message for entry in entries)
            assert any("logging stuck" in entry.message for entry in entries)
            assert all(entry.run_id == identifier for entry in entries)
            assert await queue.worker_command(ready.read_text())
            stdout, stderr = await asyncio.wait_for(worker.communicate(), 5)
            assert worker.returncode == 0, (stdout.decode(), stderr.decode())
            assert await worker_state(queue, "stopped")
            assert (await state(queue, identifier, "cancelled")).attempt == 1
        finally:
            if worker.returncode is None:
                await queue.worker_command(ready.read_text(), force=True)
                try:
                    await asyncio.wait_for(worker.wait(), 5)
                except TimeoutError:
                    worker.kill()
                    await worker.wait()
            await dispatcher.close()
            if backend == "redis":
                import redis.asyncio

                admin = redis.asyncio.Redis.from_url(url)
                try:
                    await admin.delete(*queue._operations_keys)
                finally:
                    await admin.aclose()

    run(scenario())


async def logged(queue, identifier):
    return len((await queue.logs(run_id=identifier)).entries) >= 3


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def process_gone(pid):
    return not process_alive(pid)


def test_force_stop_reconciles_finished_groups_before_signalling(monkeypatch):
    import pickle
    import socket

    from anyo.execution._process import IsolatedRun

    runner = IsolatedRun(
        print,
        HookEvent("test", "ready", "event"),
        None,
        worker_id="worker",
        run_id="run",
        attempt=1,
    )
    runner._forced = runner._cancelled = True
    reader, writer = socket.socketpair(type=socket.SOCK_DGRAM)
    log_reader, log_writer = socket.socketpair(type=socket.SOCK_DGRAM)
    reader.setblocking(False)
    log_reader.setblocking(False)
    signalled = []
    monkeypatch.setattr(os, "killpg", lambda pid, signal: signalled.append(pid))
    try:
        writer.send(pickle.dumps(("process_started", 12345)))
        writer.send(pickle.dumps(("process_stopped", 12345)))
        assert runner._drain(reader, log_reader)
        assert not signalled
        assert not runner._groups
    finally:
        for stream in (reader, writer, log_reader, log_writer):
            stream.close()


def test_inaccessible_group_does_not_abort_other_owned_cleanup(monkeypatch):
    from anyo.execution._process import IsolatedRun

    runner = IsolatedRun(
        print,
        HookEvent("test", "ready", "event"),
        None,
        worker_id="worker",
        run_id="run",
        attempt=1,
    )
    runner._groups = {12345, 12346}
    attempted = []

    def kill(pid, signal):
        attempted.append(pid)
        if pid == 12345:
            raise PermissionError("Cannot signal group")

    monkeypatch.setattr(os, "killpg", kill)
    runner._kill_groups()
    assert set(attempted) == {12345, 12346}
    assert runner._group_cleanup_unconfirmed
