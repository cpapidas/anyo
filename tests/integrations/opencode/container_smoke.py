"""Run the real pinned binary against a local simulated Anthropic API.

Execute inside the built image with this file mounted read-only. No paid model
or API credentials are used. OpenCode may install its plugin support package.
"""

import asyncio
import json
import os
import signal
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from anyo import PromptClient
from anyo.integrations.opencode import opencode_harness


class ModelHandler(BaseHTTPRequestHandler):
    calls = []
    started = threading.Event()
    release = threading.Event()
    release.set()

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append(body)
        self.started.set()
        if not self.release.wait(30):
            raise AssertionError("Fixture model gate timed out")
        structured = any(t.get("name") == "StructuredOutput" for t in body.get("tools", []))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()

        def event(name, value):
            value["type"] = name
            self.wfile.write(f"event: {name}\ndata: {json.dumps(value)}\n\n".encode())
            self.wfile.flush()

        event(
            "message_start",
            {
                "message": {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                }
            },
        )
        if structured:
            event(
                "content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}
            )
            event(
                "content_block_delta",
                {
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Inspecting the supplied diff."},
                },
            )
            event("content_block_stop", {"index": 0})
            event(
                "content_block_start",
                {
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool_fixture",
                        "name": "StructuredOutput",
                        "input": {},
                    },
                },
            )
            event(
                "content_block_delta",
                {
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(
                            {
                                "type": "success",
                                "data": {"comments": [{"message": "Fixture review"}]},
                                "failure_code": None,
                                "failure_message": None,
                            }
                        ),
                    },
                },
            )
            event("content_block_stop", {"index": 1})
        else:
            event(
                "content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}
            )
            event(
                "content_block_delta",
                {"index": 0, "delta": {"type": "text_delta", "text": "Review"}},
            )
            event("content_block_stop", {"index": 0})
        event(
            "message_delta",
            {
                "delta": {
                    "stop_reason": "tool_use" if structured else "end_turn",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 20},
            },
        )
        event("message_stop", {})
        self.close_connection = True


async def exercise(root, server):
    plugin = root / "smoke-plugin.mjs"
    marker = root / "plugin-loaded"
    plugin.write_text(
        'import {writeFile} from "node:fs/promises";\n'
        "export const SmokePlugin = async () => {\n"
        f'  await writeFile({json.dumps(str(marker))}, "loaded"); return {{}};\n'
        "};\n"
    )
    prompt = root / "review.md"
    prompt.write_text("Review the supplied diff and return JSON with a comments array.")
    # Ambient repository configuration must never replace Python configuration.
    (root / "opencode.json").write_text('{"model":"incorrect/model","plugin":["missing-plugin"]}')
    handle = opencode_harness(
        models=[
            {
                "model": "anthropic/claude-sonnet-4-5",
                "key": "fixture-only-key",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            }
        ],
        plugins=[plugin.as_uri()],
        state_directory=root / "state",
        startup_timeout=30,
        control_timeout=30,
    )
    events = []

    async def observer(event):
        events.append(event)

    async with PromptClient(harness=handle) as client:
        result = await client.ainject_prompt(prompt, workspace=root, timeout=90, on_event=observer)
        if result.failure:
            raise AssertionError(f"Prompt failed: {result.failure}")
        assert result.success.data == {"comments": [{"message": "Fixture review"}]}
        assert marker.read_text() == "loaded"
        assert any(
            e.kind in {"assistant_text", "text_delta", "tool_start", "tool_end"} for e in events
        )
        assert all("fixture-only-key" not in (e.message or "") for e in events)
        assert all(r["model"] == "claude-sonnet-4-5" for r in ModelHandler.calls)
        # Two more simultaneous sessions exercise shared-child startup and routing.
        results = await asyncio.gather(
            *[client.ainject_prompt(prompt, workspace=root, timeout=90) for _ in range(2)]
        )
        assert all(r.success is not None for r in results)
        assert len({result.session_id, *(r.session_id for r in results)}) == 3
        # A killed child must fail active work, then restart for a later call.
        ModelHandler.started.clear()
        ModelHandler.release.clear()
        pending = asyncio.create_task(client.ainject_prompt(prompt, workspace=root, timeout=60))
        try:
            assert await asyncio.to_thread(ModelHandler.started.wait, 30)
            owned = client._backend.server.process
            os.kill(owned.pid, signal.SIGKILL)
            crashed = await pending
            assert crashed.failure.code == "anyo.server_exited", crashed
        finally:
            ModelHandler.release.set()
        recovered = await client.ainject_prompt(prompt, workspace=root, timeout=60)
        assert recovered.success is not None, recovered
        async with PromptClient(harness=handle) as other:
            separate = await other.ainject_prompt(prompt, workspace=root, timeout=60)
            assert separate.success is not None, separate
            assert client._backend.server.url != other._backend.server.url
            assert client._backend.server.state.name != other._backend.server.state.name
    assert not list((root / "state").iterdir()), "Owned state was not cleaned up"
    broken = root / "broken-plugin.mjs"
    broken.write_text('export const Broken = async () => { throw new Error("fixture"); };')
    before = len(ModelHandler.calls)
    invalid = opencode_harness(
        models=[
            {
                "model": "anthropic/claude-sonnet-4-5",
                "key": "fixture-only-key",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            }
        ],
        plugins=[broken.as_uri()],
        state_directory=root / "state",
        control_timeout=30,
    )
    async with PromptClient(harness=invalid) as client:
        failure = await client.ainject_prompt(prompt, workspace=root, timeout=60)
        assert failure.failure is not None
        assert failure.failure.code == "anyo.configuration_failed", failure
    assert len(ModelHandler.calls) == before, "A broken plugin must prevent model calls"
    assert not list((root / "state").iterdir())
    print(
        json.dumps(
            {
                "success": True,
                "uid": os.getuid(),
                "model_calls": len(ModelHandler.calls),
                "progress_kinds": sorted({e.kind for e in events}),
                "successful_sessions": 5,
                "crash_recovery": True,
                "plugin_failure": True,
                "independent_runtimes": True,
            }
        )
    )


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="anyo-smoke-") as directory:
            asyncio.run(exercise(Path(directory), server))
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
