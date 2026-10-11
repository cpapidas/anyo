"""Owned native diagnostics reach operations storage only after redaction."""

import asyncio

from anyo.agents import PromptClient
from anyo.integrations.omo import omo_harness
from anyo.observability import OperationsLogSession, log_scope, process_scope


class Sink:
    def __init__(self):
        self.entries = []

    async def append_logs(self, entries):
        self.entries.extend(entries)


def test_native_stderr_tools_and_reasoning_are_independent_of_observers(executable, tmp_path):
    sink = Sink()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")
    harness = omo_harness(
        models=[{"model": "minimax/MiniMax-M2.7", "key": "secret-sentinel"}],
        executable=str(executable),
        shutdown_timeout=0.3,
    )
    started, stopped = [], []

    async def exercise():
        async with OperationsLogSession(sink):
            with log_scope(run_id="job", attempt=1), process_scope(started.append, stopped.append):
                async with PromptClient(harness=harness) as client:
                    first = await client.ainject_prompt(prompt, workspace=tmp_path)
                    assert first.success.data == {"ok": True}
                    prompt.write_text("CRASH")
                    second = await client.ainject_prompt(prompt, workspace=tmp_path)
                    assert second.failure.code == "anyo.process_failed"

    asyncio.run(exercise())
    assert started == stopped and len(started) == 4  # Two version probes and two RPC processes.
    assert not any("secret-sentinel" in entry.message for entry in sink.entries)
    assert not any("private reasoning" in entry.message for entry in sink.entries)
    assert any(
        entry.source == "omo.stderr" and "[redacted]" in entry.message for entry in sink.entries
    )
    assert any(
        entry.source == "omo.tools" and '"toolName": "read"' in entry.message
        for entry in sink.entries
    )
    assert {entry.run_id for entry in sink.entries} == {"job"}
    assert len({entry.prompt_id for entry in sink.entries}) == 2
