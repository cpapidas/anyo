"""Verify transport behavior and cleanup through the public Python API."""

import asyncio
import os
import signal
import stat
import sys

import pytest

from anyo import PromptClient
from anyo.integrations.opencode import opencode_harness


def harness(server, **options):
    return opencode_harness(
        models=[{"model": "anthropic/test", "key": "model-key"}],
        server_url=server.url,
        server_password="server-key",
        poll_interval=0.01,
        control_timeout=0.3,
        **options,
    )


@pytest.mark.parametrize("mode", ["success", "disconnect", "ambiguous", "pending_format"])
def test_final_result_after_acceptance_and_reconnection(server, prompt, mode):
    server.mode = mode
    with PromptClient(harness=harness(server)) as client:
        result = client.inject_prompt(prompt, workspace=prompt.parent, context={"pr": 12})
    assert result.success.data == {"session": "ses_1"}
    assert len(server.prompts) == 1
    sid, body, directory = server.prompts[0]
    assert directory == str(prompt.parent)
    assert "Anyo result contract" in body["system"]
    assert body["format"]["type"] == "json_schema"
    assert body["model"] == {"providerID": "anthropic", "modelID": "test"}
    assert '"pr": 12' in body["parts"][0]["text"]
    assert server.deleted == [sid]
    assert server.aborted == []
    # Closing a borrowed runtime does not stop its server.
    assert server.thread.is_alive()
    if mode == "pending_format":
        assert server.format_errors == 1


def test_reserved_output_tool_cannot_be_disabled():
    with pytest.raises(ValueError, match="StructuredOutput"):
        opencode_harness(
            models=[{"model": "p/m", "key": "k"}],
            permissions={"*": "deny", "StructuredOutput": "deny"},
        )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("invalid", "invalid_output"),
        ("missing", "invalid_output"),
        ("provider_error", "provider_error"),
        ("interaction", "interaction_required"),
    ],
)
def test_failure_cleanup_and_sanitized_errors(server, prompt, mode, code):
    server.mode = mode
    if mode == "interaction":
        server.release.clear()
    with PromptClient(harness=harness(server)) as client:
        result = client.inject_prompt(prompt, timeout=2)
    assert result.success is None
    assert result.failure.code == "anyo." + code
    assert "do-not-expose-key" not in result.failure.message
    assert server.aborted == ["ses_1"]
    assert server.deleted == ["ses_1"]


def test_progress_before_answer_and_session_isolation(server, prompt):
    server.release.clear()

    async def exercise():
        seen = []

        async def progress(event):
            seen.append(event)
            if event.kind == "assistant_text":
                server.release.set()

        async with PromptClient(harness=harness(server)) as client:
            first, second = await asyncio.gather(
                client.ainject_prompt(prompt, on_event=progress, timeout=2),
                client.ainject_prompt(prompt, timeout=2),
            )
        assert first.success and second.success
        assert first.session_id != second.session_id
        assert any(e.kind == "assistant_text" for e in seen)
        assert all(e.session_id in {None, first.session_id} for e in seen)
        assert [e.sequence for e in seen] == sorted(e.sequence for e in seen)

    asyncio.run(exercise())


def test_timeout_and_cancellation_abort_without_resubmission(server, prompt):
    server.release.clear()
    with PromptClient(harness=harness(server)) as client:
        result = client.inject_prompt(prompt, timeout=0.2)
    assert result.failure.code == "anyo.timeout"
    assert len(server.prompts) == 1
    assert server.aborted == ["ses_1"]

    async def exercise():
        async with PromptClient(harness=harness(server)) as client:
            ready = asyncio.Event()

            async def progress(event):
                if event.kind == "status" and event.message == "submitted":
                    ready.set()

            task = asyncio.create_task(client.ainject_prompt(prompt, on_event=progress))
            await asyncio.wait_for(ready.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(exercise())
    assert len(server.prompts) == 2
    assert server.aborted == ["ses_1", "ses_2"]


def test_capacity_is_rejected_without_a_second_submission(server, prompt):
    server.release.clear()

    async def exercise():
        async with PromptClient(harness=harness(server, max_concurrency=1)) as client:
            ready = asyncio.Event()

            async def progress(event):
                if event.message == "submitted":
                    ready.set()

            task = asyncio.create_task(client.ainject_prompt(prompt, on_event=progress))
            await asyncio.wait_for(ready.wait(), 2)
            rejected = await client.ainject_prompt(prompt)
            assert rejected.failure.code == "anyo.capacity_exceeded"
            server.release.set()
            assert (await task).success

    asyncio.run(exercise())
    assert len(server.prompts) == 1


def test_repeated_cancellation_waits_for_abort_and_delete(server, prompt):
    server.release.clear()
    server.abort_release.clear()

    async def exercise():
        async with PromptClient(harness=harness(server)) as client:
            task = asyncio.create_task(client.ainject_prompt(prompt))
            assert await asyncio.to_thread(server.submitted.wait, 2)
            task.cancel()
            assert await asyncio.to_thread(server.abort_started.wait, 2)
            task.cancel()
            server.abort_release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        assert server.deleted == ["ses_1"]

    asyncio.run(exercise())


def test_authentication_failure_does_not_submit(server, prompt):
    server.mode = "auth"
    with PromptClient(harness=harness(server)) as client:
        result = client.inject_prompt(prompt)
    assert result.failure.code == "anyo.authentication_failed"
    assert not server.prompts


def test_configuration_and_version_mismatch_prevent_submission(server, prompt):
    for change in ["version", "config"]:
        if change == "version":
            server.version = "1.0.0"
        else:
            server.version = "1.18.34"
            server.config["model"] = "someone/else"
        with PromptClient(harness=harness(server)) as client:
            result = client.inject_prompt(prompt)
        assert result.failure.code == (
            "anyo.version_mismatch" if change == "version" else "anyo.configuration_mismatch"
        )
    assert not server.prompts


def test_missing_binary_is_an_actionable_runtime_failure(prompt, tmp_path):
    handle = opencode_harness(
        models=[{"model": "p/m", "key": "k"}],
        executable=str(tmp_path / "no-binary"),
        state_directory=tmp_path,
    )
    with PromptClient(harness=handle) as client:
        result = client.inject_prompt(prompt)
    assert result.failure.code == "anyo.executable_missing"
    assert not list(tmp_path.glob("anyo-opencode-*"))


@pytest.mark.skipif(os.name != "posix", reason="Managed process groups require POSIX")
def test_hung_startup_reaps_process_group_and_removes_private_state(prompt, tmp_path):
    program = tmp_path / "fake-opencode"
    pid_file = tmp_path / "pid"
    program.write_text(
        f"#!{sys.executable}\nimport os, signal\n"
        f'open({str(pid_file)!r}, "w").write(str(os.getpid()))\n'
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\nsignal.pause()\n"
    )
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    handle = opencode_harness(
        models=[{"model": "p/m", "key": "k"}],
        executable=str(program),
        startup_timeout=2,
        shutdown_timeout=0.1,
        state_directory=tmp_path,
    )
    with PromptClient(harness=handle) as client:
        result = client.inject_prompt(prompt, timeout=4)
    assert result.failure.code == "anyo.startup_timeout"
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, signal.SIGCONT)
    assert not list(tmp_path.glob("anyo-opencode-*"))
