import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import cast

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
                for room_code in tuple(registry.rooms):
                    await registry.remove_room(room_code)
            finally:
                app.state.room_registry = None


def create_app(
    registry: RoomRegistry | None = None,
    token_ttl: timedelta = timedelta(hours=6),
    latency: LatencyRecorder | None = None,
    reaper_interval_seconds: float = 60.0,
) -> FastAPI:
    app = FastAPI(title="Werewolf DM S2", version="0.2.0", lifespan=lifespan)
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

    return app
