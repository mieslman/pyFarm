import json

import pytest
from fastapi.testclient import TestClient

from app.api.websocket import ws_manager
from app.main import app

client = TestClient(app)


def test_websocket_connection_and_status():
    """Test connecting to the /ws/live endpoint and receiving initial status."""
    with client.websocket_connect("/ws/live") as websocket:
        # Initial message should be status
        data = websocket.receive_text()
        msg = json.loads(data)
        assert msg["type"] == "status"
        assert "state" in msg["data"]
        assert "is_running" in msg["data"]

        # Ping pong test
        websocket.send_text("ping")
        pong_data = websocket.receive_text()
        pong_msg = json.loads(pong_data)
        assert pong_msg["type"] == "pong"


@pytest.mark.asyncio
async def test_websocket_broadcast():
    """Test broadcasting to connected WebSockets."""
    with client.websocket_connect("/ws/live") as websocket:
        # Receive initial status
        websocket.receive_text()

        # Broadcast custom test event
        await ws_manager.broadcast({"type": "test", "data": "hello"})

        received = websocket.receive_text()
        msg = json.loads(received)
        assert msg["type"] == "test"
        assert msg["data"] == "hello"
