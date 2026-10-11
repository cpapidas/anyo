"""Exercise every Events-compatible name in the dated official catalog snapshot."""

import pytest
from starlette.testclient import TestClient

from .catalog_snapshot import EVENT_CATALOG
from .conftest import add, deliver, event_payload


@pytest.mark.parametrize(
    "row", [r for r in EVENT_CATALOG if "Events" in r["apis"]], ids=lambda row: row["name"]
)
def test_catalog_entry_routes(app, row):
    name = row["name"]
    calls = []
    if name == "url_verification":
        add(app, calls.append)
        with TestClient(app.asgi()) as client:
            response = deliver(client, {"type": name, "challenge": "catalog-challenge"})
            assert response.json() == {"challenge": "catalog-challenge"}
        assert not calls
        return
    if name == "app_rate_limited":
        add(app, calls.append, action="control", event_type=name)
        payload = {
            "type": name,
            "team_id": "T123",
            "api_app_id": "A123",
            "minute_rate_limited": 1780000000,
        }
    else:
        config = {"event_type": name}
        native = name
        fields = {"opaque_field": {"numbers": [1, 2], "future": True}}
        if name.startswith("message/"):
            subtype = name.split("/")[1]
            native = "app_mention" if subtype == "document_mention" else "message"
            config = {"event_type": native, "subtype": subtype}
            fields["subtype"] = subtype
        elif name.startswith("message."):
            native = "message"
            fields["channel_type"] = {
                "channels": "channel",
                "groups": "group",
                "im": "im",
                "mpim": "mpim",
                "app_home": "app_home",
            }[name.split(".")[1]]
        add(app, calls.append, **config)
        payload = event_payload(native, **fields)
        payload["event_id"] = "Ev-" + name.replace("/", "-")
    with TestClient(app.asgi()) as client:
        response = deliver(client, payload)
        assert response.status_code == 200
        assert response.content == b""
    assert len(calls) == 1
    assert calls[0].payload == payload
