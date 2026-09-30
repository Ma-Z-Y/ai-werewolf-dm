import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from typing import cast

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from werewolf_dm.application.rooms import RoomRegistry
from werewolf_dm.domain.visibility import ProjectionAccessError
from werewolf_dm.interfaces.http_ws.audit import router as audit_router
from werewolf_dm.interfaces.http_ws.display import router as display_router
from werewolf_dm.interfaces.http_ws.errors import ErrorCode, ErrorResponse, sanitize_error
from werewolf_dm.interfaces.http_ws.metrics import LatencyRecorder, metrics_router
from werewolf_dm.interfaces.http_ws.models import HealthResponse
from werewolf_dm.interfaces.http_ws.rooms import router as rooms_router
from werewolf_dm.interfaces.http_ws.runtime import (
    build_production_registry,
    reap_periodically,
)
from werewolf_dm.interfaces.http_ws.ws import router as ws_router

_API_PATH_PREFIXES = frozenset(
    {"docs", "healthz", "metrics", "openapi.json", "redoc", "rooms", "ws"}
)


def _resolve_static_dir(static_dir: Path | None) -> Path | None:
    configured = static_dir
    if configured is None:
        configured_value = os.environ.get("WEREWOLF_DM_STATIC_DIR")
        if configured_value:
            configured = Path(configured_value).expanduser()
    if configured is None:
        return None
    resolved = configured.resolve()
    if not resolved.is_dir() or not (resolved / "index.html").is_file():
        return None
    return resolved


def _is_api_path(path: str) -> bool:
    return path in _API_PATH_PREFIXES or any(
        path.startswith(f"{prefix}/") for prefix in _API_PATH_PREFIXES
    )


def _status_for_error_code(code: ErrorCode) -> int:
    if code is ErrorCode.ROOM_NOT_FOUND:
        return 404
    if code in {ErrorCode.TOKEN_INVALID, ErrorCode.TOKEN_EXPIRED}:
        return 401
    if code is ErrorCode.ROOM_FULL:
        return 409
    if code is ErrorCode.ROOM_LIMIT_REACHED:
        return 503
    if code in {ErrorCode.ACTOR_NOT_AUTHORIZED, ErrorCode.CHANNEL_FORBIDDEN}:
        return 403
    if code is ErrorCode.RATE_LIMITED:
        return 429
    if code is ErrorCode.INTERNAL_ERROR:
        return 500
    return 400


def _error_response(exc: Exception, *, status_code: int | None = None) -> JSONResponse:
    error: ErrorResponse = sanitize_error(exc)
    return JSONResponse(
        status_code=status_code or _status_for_error_code(error.code),
        content=error.model_dump(mode="json"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    owns_registry = app.state.room_registry is None
    reaper_task = None
    if owns_registry:
        app.state.room_registry = build_production_registry()
        registry = cast(RoomRegistry, app.state.room_registry)
        try:
            await registry.start_rooms()
        except BaseException:
            await registry.close(close_store=True)
            app.state.room_registry = None
            raise
        else:
            reaper_task = asyncio.create_task(
                reap_periodically(
                    registry,
                    interval_seconds=app.state.reaper_interval_seconds,
                )
            )
    try:
        yield
    finally:
        if reaper_task is not None:
            reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await reaper_task
        if owns_registry:
            registry = cast(RoomRegistry, app.state.room_registry)
            try:
                await registry.close(close_store=True)
            finally:
                app.state.room_registry = None


def create_app(
    registry: RoomRegistry | None = None,
    token_ttl: timedelta = timedelta(hours=6),
    latency: LatencyRecorder | None = None,
    reaper_interval_seconds: float = 60.0,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Werewolf DM", version="0.4.0", lifespan=lifespan)
    latency = latency or LatencyRecorder(window=100)
    app.state.room_registry = registry
    app.state.token_ttl = token_ttl
    app.state.latency_recorder = latency
    app.state.reaper_interval_seconds = max(0.001, reaper_interval_seconds)
    app.include_router(rooms_router)
    app.include_router(audit_router)
    app.include_router(display_router)
    app.include_router(ws_router)
    app.include_router(metrics_router(registry, latency))

    async def request_validation_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        del request
        return _error_response(exc, status_code=422)

    async def projection_access_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        del request
        return _error_response(exc)

    async def websocket_disconnect_handler(
        websocket: WebSocket,
        exc: Exception,
    ) -> None:
        del websocket, exc
        return None

    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        del request
        return _error_response(exc)

    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(ProjectionAccessError, projection_access_error_handler)
    app.add_exception_handler(WebSocketDisconnect, websocket_disconnect_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(version=app.version, active_rooms=0)

    static_root = _resolve_static_dir(static_dir)
    if static_root is not None:
        assets_dir = static_root / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=assets_dir),
                name="frontend-assets",
            )

        index_path = static_root / "index.html"

        @app.get("/", include_in_schema=False)
        async def frontend_root() -> FileResponse:
            return FileResponse(index_path, headers={"Cache-Control": "no-cache"})

        @app.get("/{full_path:path}", include_in_schema=False)
        async def frontend_fallback(full_path: str) -> FileResponse:
            if _is_api_path(full_path):
                raise HTTPException(status_code=404, detail="Not Found")

            candidate = (static_root / full_path).resolve()
            try:
                candidate.relative_to(static_root)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Not Found") from exc
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_path, headers={"Cache-Control": "no-cache"})

    return app
