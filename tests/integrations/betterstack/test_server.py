"""Real local TCP listener, JSON POSTs, and background callbacks."""

import asyncio
import http.client
import json
import socket
import threading
from contextlib import closing

import uvicorn

from .conftest import HEADERS, add, error, incident


def test_listener_accepts_incident_and_error_then_releases_port(app):
    ready = threading.Event()
    received = threading.Event()
    calls = []

    def callback(event):
        calls.append(event)
        if len(calls) == 2:
            received.set()

    add(app, callback)
    add(app, callback, action="error", hook_id="error")

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)
            ready.set()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = Server(uvicorn.Config(app.asgi(), log_config=None, access_log=False))
        thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])))
        thread.start()
        try:
            assert ready.wait(5)
            with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
                for data in (incident(), error()):
                    connection.request("POST", "/webhooks/betterstack", json.dumps(data), HEADERS)
                    response = connection.getresponse()
                    assert response.status == 200
                    assert response.read() == b""
            assert received.wait(5)
        finally:
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()
    assert {e.action for e in calls} == {"incident", "error"}
