"""Real owned-process diagnostics and session-scoped tool log coverage."""

import asyncio
import sys

from anyo.agents import Model
from anyo.integrations.opencode._configuration import Settings
from anyo.integrations.opencode._process import ServerProcess
from anyo.integrations.opencode._runtime import _Run
from anyo.observability import OperationsLogSession, log_scope, process_scope, register_secrets
from anyo.prompts import PromptRequest


class Sink:
    def __init__(self):
        self.entries = []

    async def append_logs(self, entries):
        self.entries.extend(entries)


def test_owned_raw_streams_redacted_and_not_assigned_to_first_prompt(tmp_path):
    program = tmp_path / "opencode-fixture"
    program.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "print('opencode server listening on http://127.0.0.1:12345', flush=True)\n"
        "sys.stderr.write('model-secret-'); sys.stderr.flush()\n"
        "sys.stderr.write('sentinel ' + os.environ['OPENCODE_SERVER_PASSWORD'] + '\\n'); "
        "sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )
    program.chmod(0o700)
    server = ServerProcess(
        Settings(
            (Model("provider/test", "model-secret-sentinel"),),
            (),
            executable=str(program),
            shutdown_timeout=0.3,
            server_password="server-secret-sentinel",
        )
    )
    sink = Sink()
    started, stopped = [], []

    async def exercise():
        async with OperationsLogSession(sink, worker_id="worker"):
            with (
                log_scope(run_id="first-job", prompt_id="first-prompt"),
                process_scope(started.append, stopped.append),
            ):
                assert await server.start() == "http://127.0.0.1:12345"
                await server.close()

    asyncio.run(exercise())
    assert len(started) == 1 and stopped == started
    assert any(entry.source == "opencode.stderr" for entry in sink.entries)
    assert not any("secret-sentinel" in entry.message for entry in sink.entries)
    assert all(entry.run_id is None and entry.prompt_id is None for entry in sink.entries)
    assert all(entry.worker_id == "worker" for entry in sink.entries)


def test_tool_state_captured_without_changing_observer_contract(tmp_path):
    sink, events = Sink(), []
    request = PromptRequest("prompt", "Review", tmp_path, "provider/test", 10, False)
    run = _Run(request, events.append, session="session")

    async def exercise():
        async with OperationsLogSession(sink):
            register_secrets(["tool-secret"])
            with log_scope(run_id="job", attempt=1):
                run.update(
                    {
                        "type": "message.part.updated",
                        "properties": {
                            "part": {
                                "id": "part",
                                "messageID": "message",
                                "sessionID": "session",
                                "type": "tool",
                                "tool": "read",
                                "state": {
                                    "status": "completed",
                                    "input": {"path": "fixture"},
                                    "output": "tool-secret useful output",
                                },
                            }
                        },
                    }
                )

    asyncio.run(exercise())
    assert [event.kind for event in events] == ["tool_end"]
    assert "useful output" in sink.entries[0].message
    assert "tool-secret" not in sink.entries[0].message
    assert sink.entries[0].run_id == "job" and sink.entries[0].prompt_id == "prompt"
