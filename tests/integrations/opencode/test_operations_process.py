"""Force stop reaches a stuck managed OpenCode startup from its run supervisor."""

import asyncio
import importlib
import os
import sys
import uuid

from anyo.execution._codec import EventCodecRegistry
from anyo.execution._contracts import QueueSettings
from anyo.execution._operations import OperationsSettings
from anyo.execution._worker import DurableDispatcher
from anyo.hooks import Hook, HookEvent
from anyo.integrations.opencode import opencode_harness
from anyo.integrations.sqlite import SqliteQueue


def test_force_run_reaps_opencode_group_during_hung_startup(tmp_path, monkeypatch):
    module_name = f"anyo_opencode_process_{uuid.uuid4().hex}"
    (tmp_path / f"{module_name}.py").write_text(
        "import anyo\ndef pipeline(event):\n    anyo.inject_prompt(event.payload['prompt'])\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    callback = importlib.import_module(module_name).pipeline
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")
    marker = tmp_path / "processes"
    executable = tmp_path / "opencode-fixture"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(os.getpid()) + ' ' + str(child.pid))\n"
        "time.sleep(60)\n"
    )
    executable.chmod(0o700)
    harness = opencode_harness(
        models=[{"model": "provider/model", "key": "test-key"}],
        executable=str(executable),
        startup_timeout=30,
        shutdown_timeout=0.2,
        state_directory=tmp_path / "state",
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
        )
        await dispatcher.start()
        try:
            await dispatcher.submit(
                [(hook, HookEvent("test", "ready", "one", payload={"prompt": str(prompt)}))]
            )
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            pids = [int(value) for value in marker.read_text().split()]
            identifier = (await queue.inspect())[0].id
            assert await queue.cancel(identifier, force=True)
            await dispatcher.wait_idle()
            assert (await queue.inspect())[0].status == "cancelled"
            for pid in pids:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                raise AssertionError("Owned OpenCode subprocess survived force stop")
        finally:
            for attempt in tuple(dispatcher._active.values()):
                if attempt.process:
                    attempt.process.cancel(force=True)
            await dispatcher.close()

    async def bounded():
        await asyncio.wait_for(scenario(), 15)

    asyncio.run(bounded())
