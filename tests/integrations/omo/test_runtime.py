"""Consumer contracts for native OmO, with no network or model requests."""

import asyncio
import json
import os
import signal
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import anyo
from anyo.application import Application
from anyo.integrations.omo import _runtime as runtime
from anyo.integrations.omo import omo_harness
from anyo.integrations.omo._configuration import Settings

MODEL = "minimax/MiniMax-M2.7"


def harness(executable, tmp_path, **options):
    return omo_harness(
        models=[{"model": MODEL, "key": "secret-sentinel"}],
        executable=str(executable),
        state_directory=tmp_path / "state",
        shutdown_timeout=0.3,
        **options,
    )


def test_inert_configuration_and_application_choice(tmp_path):
    models = [{"model": MODEL, "key": "secret-sentinel"}]
    h = omo_harness(models=models, executable="missing-omo", state_directory=tmp_path / "absent")
    models[0]["key"] = "changed"
    assert h.provider == "omo" and h.models[0].key == "secret-sentinel"
    assert "secret-sentinel" not in repr(h)
    assert not (tmp_path / "absent").exists()
    app = Application(harnesses={"omo": omo_harness})
    assert app.harness(provider="omo", models=models).provider == "omo"
    # Verify the public composition root exposes both built-in choices.
    assert set(anyo._application._harnesses) >= {"omo", "opencode"}


@pytest.mark.parametrize(
    "options",
    [
        {"plugins": ["oh-my-openagent"]},
        {"plugins": "plugin"},
        {"tools": "read"},
        {"tools": ["*"]},
        {"tools": [None]},
        {"max_concurrency": 0},
        {"startup_timeout": float("nan")},
        {"shutdown_timeout": True},
        {"unknown": True},
        {"executable": ""},
        {"state_directory": 10},
    ],
)
def test_configuration_rejects_unsupported_or_invalid_options(options):
    with pytest.raises((TypeError, ValueError)):
        omo_harness(models=[{"model": MODEL, "key": "key"}], **options)


@pytest.mark.parametrize(
    "models",
    [
        [],
        [{"model": "invalid", "key": "key"}],
        [{"model": MODEL, "key": ""}],
        [{"model": MODEL, "key": "key", "extra": "x"}],
        [{"model": MODEL, "key": "key", "base_url": "https://user:secret@example.test"}],
        [{"model": MODEL, "key": "key"}, {"model": MODEL, "key": "key"}],
        [{"model": MODEL, "key": "key"}, {"model": "minimax/MiniMax-M2.5", "key": "other"}],
    ],
)
def test_model_validation(models):
    with pytest.raises(ValueError):
        omo_harness(models=models)


def test_success_progress_isolation_and_fresh_sessions(executable, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key")
    monkeypatch.setenv("OMO_PROFILE", "unrelated")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")
    events = []
    with anyo.PromptClient(harness=harness(executable, tmp_path)) as client:
        first = client.inject_prompt(
            prompt, context={"pr": 7}, workspace=tmp_path, on_event=events.append
        )
        second = client.inject_prompt(prompt, workspace=tmp_path)
    assert first.success.data == {"ok": True}
    assert first.session_id != second.session_id
    assert "reasoning_delta" not in {event.kind for event in events}
    assert any(e.message == "public [redacted]" for e in events)
    assert events[-1].kind == "terminal"
    assert {"model", "tool_start", "tool_end"} <= {event.kind for event in events}
    assert not list((tmp_path / "state").iterdir())


@pytest.mark.parametrize(
    "prompt_text,code",
    [
        ("INVALID", "invalid_output"),
        ("MISSING", "invalid_output"),
        ("INTERACTION", "interaction_required"),
        ("PROVIDER_ERROR", "provider_error"),
        ("WRONG_MODEL", "configuration_mismatch"),
        ("BAD_JSON", "protocol_error"),
        ("OVERSIZED", "protocol_error"),
        ("CRASH", "process_failed"),
        ("REJECT", "request_rejected"),
    ],
)
def test_failures_are_bounded_and_sanitized(executable, tmp_path, prompt_text, code):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(prompt_text)
    with anyo.PromptClient(harness=harness(executable, tmp_path)) as client:
        result = client.inject_prompt(prompt, workspace=tmp_path, timeout=3)
    assert result.failure.code == "anyo." + code
    assert "secret-sentinel" not in repr(result)
    assert not list((tmp_path / "state").iterdir())


def test_missing_bridge_fails_before_prompt_submission(executable, tmp_path):
    (tmp_path / "no-bridge").touch()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Never submit this")
    with anyo.PromptClient(harness=harness(executable, tmp_path)) as client:
        result = client.inject_prompt(prompt, workspace=tmp_path)
    assert result.failure.code == "anyo.configuration_mismatch"
    assert not (tmp_path / "received.json").exists()


def test_large_valid_prompt_survives_json_escaping(executable, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text('"' * 600_000)
    with anyo.PromptClient(harness=harness(executable, tmp_path)) as client:
        result = client.inject_prompt(prompt, workspace=tmp_path)
    assert result.success.data == {"ok": True}
    assert json.loads((tmp_path / "received.json").read_text())["message"] == prompt.read_text()


def test_agent_failure_preserves_contract(executable, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("AGENT_FAILURE")
    with anyo.PromptClient(harness=harness(executable, tmp_path)) as client:
        result = client.inject_prompt(prompt, workspace=tmp_path, include_reasoning=True)
    assert result.failure.code == "missing_fixture"
    assert result.failure.origin == "agent"


def test_timeout_cleans_process_group(executable, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("HANG")
    with anyo.PromptClient(harness=harness(executable, tmp_path)) as client:
        result = client.inject_prompt(prompt, workspace=tmp_path, timeout=0.7)
    assert result.failure.code == "anyo.timeout"
    pid = json.loads((tmp_path / "received.json").read_text())["pid"]
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)
    assert not list((tmp_path / "state").iterdir())


@pytest.mark.parametrize("denied_signal", [signal.SIGTERM, signal.SIGKILL])
def test_cleanup_waits_for_darwin_zombie_group_to_disappear(monkeypatch, denied_signal):
    async def exercise():
        clock = [0.0]
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "time", lambda: clock[0])
        monkeypatch.setattr(sys, "platform", "darwin")

        async def yield_to_reaper(delay):
            clock[0] += delay

        monkeypatch.setattr(runtime.asyncio, "sleep", yield_to_reaper)
        denials = iter([PermissionError(1, "Zombie group"), ProcessLookupError()])

        def kill_group(pid, signum):
            if signum == denied_signal:
                raise next(denials)

        monkeypatch.setattr(runtime.os, "killpg", kill_group)
        stopped = []
        monkeypatch.setattr(runtime, "notify_process_stopped", stopped.append)
        process = SimpleNamespace(pid=12345, stdin=None, wait=AsyncMock(return_value=0))
        owned = runtime._Process(Settings(models=(), shutdown_timeout=0.03))
        owned.process = process
        await owned.close()
        process.wait.assert_awaited()
        assert stopped == [process.pid]
        assert 0 < clock[0] <= owned.settings.shutdown_timeout

    asyncio.run(exercise())


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("denied_signal", [signal.SIGTERM, signal.SIGKILL])
def test_cleanup_preserves_permission_failures(monkeypatch, platform, denied_signal):
    async def exercise():
        clock = [0.0]
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "time", lambda: clock[0])
        monkeypatch.setattr(sys, "platform", platform)

        async def yield_to_reaper(delay):
            clock[0] += delay

        monkeypatch.setattr(runtime.asyncio, "sleep", yield_to_reaper)
        denied = PermissionError(1, "Permission denied")

        def kill_group(pid, signum):
            if signum == denied_signal:
                raise denied

        monkeypatch.setattr(runtime.os, "killpg", kill_group)
        owned = runtime._Process(Settings(models=(), shutdown_timeout=0.03))
        owned.process = SimpleNamespace(pid=12345, stdin=None, wait=AsyncMock(return_value=0))
        with pytest.raises(PermissionError) as raised:
            await owned.close()
        assert raised.value is denied
        assert clock[0] == (owned.settings.shutdown_timeout if platform == "darwin" else 0)

    asyncio.run(exercise())


def test_cancellation_and_capacity(executable, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("HANG")

    async def exercise():
        async with anyo.PromptClient(
            harness=harness(executable, tmp_path, max_concurrency=1)
        ) as client:
            first = asyncio.create_task(client.ainject_prompt(prompt, workspace=tmp_path))
            async with asyncio.timeout(3):
                while not (tmp_path / "received.json").exists():
                    await asyncio.sleep(0.01)
            result = await client.ainject_prompt(prompt, workspace=tmp_path)
            assert result.failure.code == "anyo.capacity_exceeded"
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

    asyncio.run(exercise())
    assert not list((tmp_path / "state").iterdir())


def test_missing_executable_and_version_mismatch(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")
    with anyo.PromptClient(harness=harness(tmp_path / "absent", tmp_path)) as client:
        result = client.inject_prompt(prompt)
    assert result.failure.code == "anyo.executable_missing"
    with anyo.PromptClient(harness=harness("/usr/bin/true", tmp_path)) as client:
        result = client.inject_prompt(prompt)
    assert result.failure.code == "anyo.version_mismatch"
