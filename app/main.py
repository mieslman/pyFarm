import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api.router import api_router
from app.api.websocket import loguru_websocket_sink, ws_manager, ws_router
from app.config import settings
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for FastAPI startup and shutdown."""
    logger.info("Starting MyFreeFarm Python Engine...")

    # Hydrate configuration from data/user_config.json
    settings_manager.load_settings()

    loop = asyncio.get_running_loop()
    ws_manager.set_event_loop(loop)

    # Register WebSocket log broadcast sink
    sink_id = logger.add(loguru_websocket_sink, level="INFO")

    task = asyncio.create_task(worker_scheduler.start())
    worker_scheduler._background_task = task
    yield
    logger.info("Shutting down MyFreeFarm Python Engine...")
    worker_scheduler.stop()
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (TimeoutError, asyncio.CancelledError):
        pass
    finally:
        logger.remove(sink_id)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS configuration matching the existing Express.js setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API & WebSocket routers
app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(ws_router)

# Mount static files directory if available
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root(request: Request):
    """Serve web dashboard for browsers, JSON status for API clients."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)

    return {
        "status": "online",
        "app": settings.app_name,
        "docs": "/docs",
        "dashboard": "/dashboard",
    }


@app.get("/dashboard")
async def dashboard():
    """Serve web dashboard UI."""
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return JSONResponse({"error": "Dashboard not found"}, status_code=404)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


