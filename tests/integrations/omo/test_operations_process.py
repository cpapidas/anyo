"""Built-in harness configuration and detached group ownership under spawn."""

import asyncio
import importlib
import json
import os
import uuid

from anyo.execution._codec import EventCodecRegistry
from anyo.execution._contracts import QueueSettings
from anyo.execution._operations import OperationsSettings
from anyo.execution._worker import DurableDispatcher
from anyo.hooks import Hook, HookEvent
from anyo.integrations.omo import omo_harness
from anyo.integrations.sqlite import SqliteQueue


async def until(predicate):
    async with asyncio.timeout(10):
        while not (value := await predicate()):
            await asyncio.sleep(0.01)
        return value


async def exists(path):
    return path.exists()


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def stopped(queue):
    return all(record.status == "cancelled" for record in await queue.inspect())


def test_force_worker_cleans_owned_native_harness_and_its_child(executable, tmp_path, monkeypatch):
    module_name = f"anyo_omo_process_{uuid.uuid4().hex}"
    (tmp_path / f"{module_name}.py").write_text(
        "import anyo\n"
        "def pipeline(event):\n"
        "    anyo.inject_prompt(event.payload['prompt'], workspace=event.payload['workspace'])\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    callback = importlib.import_module(module_name).pipeline
    prompt = tmp_path / "prompt.md"
    prompt.write_text("HANG")
    harness = omo_harness(
        models=[{"model": "minimax/MiniMax-M2.7", "key": "secret-sentinel"}],
        executable=str(executable),
        state_directory=tmp_path / "state",
        shutdown_timeout=0.2,
    )

    async def scenario():
        queue = SqliteQueue(f"sqlite:///{tmp_path / 'queue.db'}", "harness", QueueSettings())
        hook = Hook("local", "test", "ready", {}, callback, stable_key="harness")
        dispatcher = DurableDispatcher(
            queue,
            [hook],
            EventCodecRegistry(),
            workers=1,
            execution_mode="process",
            harness=harness,
            operations=OperationsSettings(),
            poll_interval=0.01,
            redaction_secrets=["secret-sentinel"],
        )
        await dispatcher.start()
        try:
            await dispatcher.submit(
                [
                    (
                        hook,
                        HookEvent(
                            "test",
                            "ready",
                            "one",
                            payload={
                                "prompt": str(prompt),
                                "workspace": str(tmp_path),
                            },
                        ),
                    )
                ]
            )
            await until(lambda: exists(tmp_path / "grandchild.pid"))
            native_pid = json.loads((tmp_path / "received.json").read_text())["pid"]
            child_pid = int((tmp_path / "grandchild.pid").read_text())
            assert alive(native_pid) and alive(child_pid)
            assert await queue.worker_command(dispatcher.worker_id, force=True)
            await dispatcher.wait_stopped()
            assert await stopped(queue)
            assert not alive(native_pid)
            assert not alive(child_pid)
            entries = (await queue.logs()).entries
            assert all("secret-sentinel" not in entry.message for entry in entries)
        finally:
            for attempt in tuple(dispatcher._active.values()):
                if attempt.process:
                    attempt.process.cancel(force=True)
            await dispatcher.close()

    async def bounded():
        await asyncio.wait_for(scenario(), 15)

    asyncio.run(bounded())
