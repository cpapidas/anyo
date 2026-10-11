"""Opt-in diagnostic capture, correlation, redaction, and bounded failures."""

import asyncio
import logging
import sys
import threading
from contextvars import Context

from anyo.agents import Harness, Model, PromptClient, PromptEvent, PromptFailure, PromptResult
from anyo.observability import (
    DiagnosticStream,
    OperationsLogSession,
    emit_log,
    ipc_log_scope,
    log_scope,
    process_scope,
)


class Sink:
    def __init__(self):
        self.entries = []

    async def append_logs(self, entries):
        self.entries.extend(entries)


def test_context_across_tasks_threads_and_host_configuration(capsys):
    sink = Sink()
    root = logging.getLogger()
    prior_handlers, prior_level = list(root.handlers), root.level
    logger = logging.getLogger("consumer.operations-test")
    prior_logger_level = logger.level
    logger.setLevel(logging.INFO)
    original_stdout, original_stderr = sys.stdout, sys.stderr

    async def exercise():
        async with OperationsLogSession(sink, worker_id="worker"):
            emit_log("receiver ready", source="receiver")
            ready = asyncio.Event()

            async def run(identifier):
                with log_scope(run_id=identifier, attempt=2):
                    ready.set()
                    await ready.wait()
                    await asyncio.to_thread(logger.info, "log from %s", identifier)
                    await asyncio.to_thread(print, "output from " + identifier)
                    with log_scope(prompt_id=identifier + "-prompt"):
                        emit_log("prompt work", source="fake.prompt")

            await asyncio.gather(run("one"), run("two"))
            assert root.handlers[:-1] == prior_handlers
            assert root.level == prior_level

    try:
        asyncio.run(exercise())
    finally:
        logger.setLevel(prior_logger_level)
    assert root.handlers == prior_handlers
    assert root.level == prior_level
    assert sys.stdout is original_stdout and sys.stderr is original_stderr
    for identifier in ("one", "two"):
        entries = [entry for entry in sink.entries if entry.run_id == identifier]
        assert {entry.source for entry in entries} >= {
            "consumer.operations-test",
            "pipeline.stdout",
            "fake.prompt",
        }
        assert all(entry.attempt == 2 and entry.worker_id == "worker" for entry in entries)
        assert all(
            "from " + ({"one", "two"} - {identifier}).pop() not in e.message for e in entries
        )
        prompt = next(entry for entry in entries if entry.source == "fake.prompt")
        assert prompt.prompt_id == identifier + "-prompt"
    assert next(entry for entry in sink.entries if entry.source == "receiver").run_id is None
    assert "output from one" in capsys.readouterr().out


def test_redaction_before_sink_across_writes_chunks_exceptions_and_truncation(monkeypatch):
    sink = Sink()
    monkeypatch.setenv("ANYO_UI_PASSWORD", "env-password-sentinel")

    async def exercise():
        async with OperationsLogSession(
            sink, secrets=("configured-secret", "first-line\nsecond-line"), max_entry_bytes=160
        ):
            sys.stdout.write("configured-")
            sys.stdout.flush()
            sys.stdout.write("secret\n")
            sys.stderr.write("env-password-sentinel\n")
            stream = DiagnosticStream("omo.stderr")
            stream.feed(b"configured-")
            stream.feed(b"secret\n")
            stream.feed(b"first-line\nsecond-line\n", final=True)
            try:
                raise ValueError("env-password-sentinel configured-secret")
            except ValueError:
                logging.getLogger("consumer").exception("request failed")
            emit_log("Authorization: Bearer unknown-secret-token")
            emit_log("x" * 148 + "configured-secret" + "tail" * 50)

    asyncio.run(exercise())
    messages = "\n".join(entry.message for entry in sink.entries)
    for secret in (
        "env-password-sentinel",
        "configured-secret",
        "first-line",
        "second-line",
        "unknown-secret-token",
    ):
        assert secret not in messages
    assert "[redacted]" in messages
    assert "entry truncated" in messages
    assert all(len(entry.message.encode()) <= 160 for entry in sink.entries)
    assert not any("configured-" in entry.message for entry in sink.entries)


def test_overflow_and_oversized_partial_lines_report_gaps():
    sink = Sink()

    async def exercise():
        async with OperationsLogSession(sink, max_entries=4, max_entry_bytes=128):
            for number in range(30):
                emit_log(f"entry {number}")
            sys.stdout.write("x" * 70000)
            sys.stdout.write("ending\n")
            emit_log("last usable entry")

    asyncio.run(exercise())
    assert any("Log gap:" in entry.message for entry in sink.entries)
    assert any(entry.message == "last usable entry" for entry in sink.entries)
    assert not any("ending" in entry.message or "xxxx" in entry.message for entry in sink.entries)
    assert len(sink.entries) <= 5


def test_stalled_storage_does_not_block_producer_and_close_is_bounded():
    entered = None

    class StalledSink:
        async def append_logs(self, entries):
            entered.set()
            await asyncio.Event().wait()

    async def exercise():
        nonlocal entered
        entered = asyncio.Event()
        session = OperationsLogSession(StalledSink(), flush_timeout=0.02)
        async with session:
            emit_log("before stall")
            await asyncio.wait_for(entered.wait(), 1)
            for _ in range(600):
                emit_log("producer keeps running")
        assert session.last_error == "FlushTimeout"
        assert session.unflushed_count > 0
        assert session._task.done()

    asyncio.run(exercise())


def test_close_from_another_task_and_ipc_capture():
    sink = Sink()
    ipc = []

    async def exercise():
        session = OperationsLogSession(sink)
        await session.__aenter__()
        emit_log("normal diagnostic")
        await asyncio.create_task(session.__aexit__(None, None, None))
        emit_log("closed scope is inert")

    asyncio.run(exercise())
    assert [entry.message for entry in sink.entries] == ["normal diagnostic"]
    with ipc_log_scope(ipc.append, secrets=("ipc-secret",), run_id="child", attempt=3):
        print("child ipc-secret")
        emit_log("child activity", source="runtime")
    assert all(entry.run_id == "child" and entry.attempt == 3 for entry in ipc)
    assert any(entry.message == "child [redacted]" for entry in ipc)
    assert not any("ipc-secret" in entry.message for entry in ipc)


def test_ambiguous_general_logs_do_not_cross_namespaces():
    first, second = Sink(), Sink()

    async def exercise():
        async with OperationsLogSession(
            first, namespace="first", secrets=("first-app-secret",)
        ) as session:
            Context().run(emit_log, "single receiver general activity")
            async with OperationsLogSession(
                second, namespace="second", secrets=("second-app-secret",)
            ):
                emit_log("second-app-secret scoped message")
                Context().run(emit_log, "ambiguous first-app-secret second-app-secret")
                Context().run(print, "ambiguous stdout first-app-secret second-app-secret")
                Context().run(logging.getLogger("consumer").warning, "ambiguous logging")
                with log_scope(run_id="second-run", prompt_id="second-prompt"):
                    with session.bind():
                        emit_log("first-app-secret scoped message")

    asyncio.run(exercise())
    assert {entry.message for entry in first.entries} == {
        "single receiver general activity",
        "[redacted] scoped message",
    }
    assert [entry.message for entry in second.entries] == ["[redacted] scoped message"]
    assert not any("ambiguous" in entry.message for entry in first.entries + second.entries)
    assert all(entry.run_id is None and entry.prompt_id is None for entry in first.entries)


def test_prompt_capture_without_observer_preserves_failure_and_hides_reasoning(tmp_path):
    sink = Sink()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")

    class Backend:
        async def invoke(self, request, emit):
            emit(PromptEvent(request.run_id, "text_delta", "model-secret-"))
            emit(PromptEvent(request.run_id, "text_delta", "sentinel"))
            emit(PromptEvent(request.run_id, "reasoning_delta", "PRIVATE"))
            return PromptResult(
                request.run_id,
                failure=PromptFailure(None, "rejected", "model-secret-sentinel", "agent"),
            )

        async def close(self):
            pass

    harness = Harness("fixture", (Model("provider/test", "model-secret-sentinel"),), (), Backend)

    async def exercise():
        async with OperationsLogSession(sink):
            with log_scope(run_id="queue-run", attempt=1):
                async with PromptClient(harness=harness) as client:
                    result = await client.ainject_prompt(prompt, workspace=tmp_path)
                assert result.failure.message == "model-secret-sentinel"
                assert result.failure.code == "rejected"

    asyncio.run(exercise())
    messages = "\n".join(entry.message for entry in sink.entries)
    assert "model-secret" not in messages and "PRIVATE" not in messages
    assert "Prompt outcome: failure" in messages
    assert any(
        entry.source == "fixture.text" and entry.message == "[redacted]" for entry in sink.entries
    )
    assert all(entry.run_id == "queue-run" and entry.prompt_id for entry in sink.entries)


def test_sync_prompt_context_transfers_to_client_loop(tmp_path):
    sink = Sink()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")

    class Backend:
        async def invoke(self, request, emit):
            emit(PromptEvent(request.run_id, "status", "working"))
            return PromptResult(
                request.run_id, failure=PromptFailure(None, "fail", "safe", "agent")
            )

        async def close(self):
            pass

    harness = Harness("fixture", (Model("provider/test", "a-secret-key"),), (), Backend)

    def call():
        with log_scope(run_id="thread-run", attempt=2):
            with PromptClient(harness=harness) as client:
                return client.inject_prompt(prompt, workspace=tmp_path)

    async def exercise():
        async with OperationsLogSession(sink):
            result = await asyncio.to_thread(call)
            assert result.failure.code == "fail"

    asyncio.run(exercise())
    assert all(entry.run_id == "thread-run" and entry.prompt_id for entry in sink.entries)
    assert any("working" in entry.message for entry in sink.entries)


def test_process_callbacks_preserve_supervisor_context_in_tasks():
    from anyo.observability import notify_process_started, notify_process_stopped

    calls = []

    async def child():
        notify_process_started(42)
        notify_process_stopped(42)

    async def exercise():
        with process_scope(
            lambda pid: calls.append(("start", pid)), lambda pid: calls.append(("stop", pid))
        ):
            await asyncio.create_task(child())

    asyncio.run(exercise())
    assert calls == [("start", 42), ("stop", 42)]
    assert not any(thread.name == "anyo-log-ipc" for thread in threading.enumerate())
