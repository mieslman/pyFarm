import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.core.auth import decode_access_token
from app.worker.scheduler import worker_scheduler

ws_router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections for live log and status broadcasts."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug(
            f"WebSocket Client verbunden. Aktive Verbindungen: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.debug(
                f"WebSocket Client getrennt. Verbleibende Verbindungen: {len(self.active_connections)}"
            )

    async def broadcast(self, message: dict[str, Any]):
        """Broadcast JSON message to all connected clients."""
        if not self.active_connections:
            return

        payload = json.dumps(message)
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(payload)
            except Exception:  # noqa: BLE001
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)

    def broadcast_from_thread(self, message: dict[str, Any]):
        """Thread-safe enqueue for logging sink."""
        if not self.active_connections or not self._loop or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self._loop)
        except Exception:  # noqa: BLE001, S110
            pass


ws_manager = ConnectionManager()


def loguru_websocket_sink(message):
    """Sink function for Loguru to broadcast logs over WebSocket."""
    record = message.record
    log_entry = {
        "type": "log",
        "data": {
            "timestamp": record["time"].strftime("%H:%M:%S"),
            "level": record["level"].name,
            "module": record["name"],
            "line": record["line"],
            "message": record["message"],
        },
    }
    ws_manager.broadcast_from_thread(log_entry)


@ws_router.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """Realtime WebSocket endpoint streaming bot status and logs."""
    # Optional token verification from query params
    token = websocket.query_params.get("token")
    if token:
        try:
            decode_access_token(token)
        except Exception:  # noqa: BLE001
            await websocket.close(code=4001, reason="Ungültiges Authentifizierungs-Token")
            return

    await ws_manager.connect(websocket)

    # Send initial status state immediately
    next_seconds = None
    if worker_scheduler.next_run_time:
        import time
        now = time.time()
        next_seconds = max(0, int(worker_scheduler.next_run_time - now))

    await websocket.send_text(
        json.dumps(
            {
                "type": "status",
                "data": {
                    "state": worker_scheduler.current_state,
                    "is_running": worker_scheduler.is_running,
                    "next_run_seconds": next_seconds,
                    "last_error": worker_scheduler.last_error,
                },
            }
        )
    )

    try:
        while True:
            data = await websocket.receive_text()
            # Respond to client ping or command
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"WebSocket Verbindung beendet: {e}")
        ws_manager.disconnect(websocket)
