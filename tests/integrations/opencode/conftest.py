"""A real local HTTP/SSE fixture with no model, network credentials, or OpenCode binary."""

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import pytest

from anyo.agents import Model
from anyo.integrations.opencode._configuration import Settings
from anyo.integrations.opencode._process import native_config


class FakeOpenCode:
    def __init__(self):
        self.version = "1.18.34"
        self.config = native_config(Settings((Model("anthropic/test", "model-key"),), ()))
        self.sessions = {}
        self.streams = []
        self.requests = []
        self.prompts = []
        self.aborted = []
        self.abort_started = threading.Event()
        self.abort_release = threading.Event()
        self.abort_release.set()
        self.deleted = []
        self.release = threading.Event()
        self.release.set()
        self.submitted = threading.Event()
        self.stop = threading.Event()
        self.connected = threading.Event()
        self.mode = "success"
        self.format_errors = 0
        self.lock = threading.Lock()
        self.next_id = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def send(self, body, status=200):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fixture.requests.append(("GET", self.path))
                if self.path == "/global/health":
                    self.send(
                        {"healthy": True, "version": fixture.version},
                        401 if fixture.mode == "auth" else 200,
                    )
                elif self.path == "/config":
                    self.send(fixture.config)
                elif self.path == "/experimental/tool/ids":
                    self.send(["read", "StructuredOutput"])
                elif self.path == "/event":
                    events = queue.Queue()
                    with fixture.lock:
                        fixture.streams.append(events)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(b'data: {"type":"server.connected","properties":{}}\n\n')
                        self.wfile.flush()
                        fixture.connected.set()
                        while not fixture.stop.is_set():
                            try:
                                event = events.get(timeout=0.05)
                            except queue.Empty:
                                continue
                            if event is None:
                                break
                            data = json.dumps(event).encode()
                            # Deliberately split bytes across writes for SSE framing.
                            self.wfile.write(b"data: " + data[:10])
                            self.wfile.flush()
                            self.wfile.write(data[10:] + b"\r\n\r\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        with fixture.lock:
                            fixture.streams.remove(events)
                    self.close_connection = True
                elif self.path == "/session/status":
                    self.send(
                        {
                            sid: {"type": "busy"}
                            for sid, user in fixture.sessions.items()
                            if user and not fixture.release.is_set()
                        }
                    )
                elif self.path.startswith("/session/") and "/message" in self.path:
                    sid = self.path.split("/")[2]
                    user = fixture.sessions.get(sid)
                    if user and fixture.mode == "pending_format" and not fixture.format_errors:
                        fixture.format_errors += 1
                        self.send(
                            {
                                "data": {
                                    "message": (
                                        'Expected OutputFormatJsonSchema at [0]["info"]["format"]'
                                    )
                                }
                            },
                            400,
                        )
                        return
                    messages = []
                    if user:
                        messages.append(
                            {"info": {"id": user, "role": "user", "sessionID": sid}, "parts": []}
                        )
                        if fixture.release.is_set():
                            info = {
                                "id": "msg_answer",
                                "role": "assistant",
                                "sessionID": sid,
                                "parentID": user,
                                "time": {"created": 2, "completed": 3},
                                "structured": {
                                    "type": "success",
                                    "data": {"session": sid},
                                    "failure_code": None,
                                    "failure_message": None,
                                },
                            }
                            if fixture.mode == "invalid":
                                info["structured"] = {"type": "success"}
                            if fixture.mode == "missing":
                                del info["structured"]
                            if fixture.mode == "provider_error":
                                info["error"] = {"name": "APIError", "message": "do-not-expose-key"}
                            messages.append({"info": info, "parts": []})
                    self.send(messages[-1:] if "limit=1" in self.path else messages)
                elif self.path in {"/permission", "/question"}:
                    self.send(
                        [
                            {"sessionID": sid}
                            for sid, user in fixture.sessions.items()
                            if fixture.mode == "interaction" and user
                        ]
                    )
                else:
                    self.send({}, 404)

            def do_POST(self):
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"null"
                )
                fixture.requests.append(("POST", self.path))
                if self.path == "/session":
                    with fixture.lock:
                        fixture.next_id += 1
                        sid = f"ses_{fixture.next_id}"
                        fixture.sessions[sid] = None
                    self.send({"id": sid})
                elif self.path.endswith("/prompt_async"):
                    sid = self.path.split("/")[2]
                    user = f"msg_user_{sid}"
                    fixture.sessions[sid] = user
                    fixture.prompts.append(
                        (sid, body, unquote(self.headers["x-opencode-directory"]))
                    )
                    fixture.submitted.set()
                    fixture.publish(
                        {
                            "type": "message.updated",
                            "properties": {"info": {"id": user, "role": "user", "sessionID": sid}},
                        }
                    )
                    fixture.publish(
                        {
                            "type": "message.part.updated",
                            "properties": {
                                "part": {
                                    "id": "part_text",
                                    "sessionID": sid,
                                    "messageID": "msg_answer",
                                    "type": "text",
                                    "text": "Inspecting the diff",
                                }
                            },
                        }
                    )
                    if fixture.mode in {"disconnect", "ambiguous"}:
                        fixture.publish(None)
                    if fixture.mode == "ambiguous":
                        self.close_connection = True
                        return
                    self.send(None, 204)
                elif self.path.endswith("/abort"):
                    fixture.aborted.append(self.path.split("/")[2])
                    fixture.abort_started.set()
                    fixture.abort_release.wait(2)
                    self.send(True)
                else:
                    self.send({}, 404)

            def do_DELETE(self):
                fixture.deleted.append(self.path.split("/")[2])
                self.send(True)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.http.server_port}"

    def publish(self, event):
        with self.lock:
            for stream in self.streams:
                stream.put(event)

    def close(self):
        self.stop.set()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


@pytest.fixture
def server():
    fake = FakeOpenCode()
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def prompt(tmp_path):
    path = tmp_path / "review.md"
    path.write_text("Review the PR; return JSON with comments.")
    return path
