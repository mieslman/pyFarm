import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.circuit_breaker import circuit_breaker
from app.worker.scheduler import worker_scheduler

bot_router = APIRouter(prefix="/bot", tags=["Bot Automation"])


@bot_router.get("/status")
async def bot_status(_: dict[str, Any] = Depends(get_current_user)):
    """Get current status, state, circuit breaker and timing of the bot automation scheduler."""
    next_seconds = None
    if worker_scheduler.next_run_time:
        next_seconds = max(0, int(worker_scheduler.next_run_time - time.time()))

    return {
        "is_running": worker_scheduler.is_running,
        "state": worker_scheduler.current_state,
        "last_run": worker_scheduler.last_run_time.isoformat() if worker_scheduler.last_run_time else None,
        "next_run_timestamp": worker_scheduler.next_run_time,
        "next_run_seconds": next_seconds,
        "last_error": worker_scheduler.last_error,
        "cycle_stats": worker_scheduler.last_cycle_results,
        "circuit_breaker": circuit_breaker.get_status(),
    }


@bot_router.post("/trigger")
async def bot_trigger(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger an immediate bot automation cycle outside of regular schedule."""
    if worker_scheduler.cycle_lock.locked():
        return {"status": "skipped", "message": "Ein Bot-Zyklus wird derzeit bereits ausgeführt."}

    # Launch in background
    asyncio.create_task(worker_scheduler.run_cycle())
    return {"status": "started", "message": "Bot-Zyklus erfolgreich angestoßen."}


@bot_router.post("/circuit-breaker/reset")
async def reset_circuit_breaker_endpoint(_: dict[str, Any] = Depends(get_current_user)):
    """Manually reset the circuit breaker and resume bot automation."""
    circuit_breaker.reset()
    if worker_scheduler.current_state == "PAUSED_CIRCUIT_BREAKER":
        worker_scheduler.current_state = "IDLE"
        worker_scheduler.last_error = None
    await worker_scheduler.broadcast_status()
    return {
        "status": "success",
        "message": "Circuit-Breaker erfolgreich zurückgesetzt.",
        "circuit_breaker": circuit_breaker.get_status(),
    }
