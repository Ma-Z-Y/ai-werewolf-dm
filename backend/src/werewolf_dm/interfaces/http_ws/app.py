from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from werewolf_dm.application.rooms import RoomRegistry
from werewolf_dm.domain.visibility import ProjectionAccessError
from werewolf_dm.interfaces.http_ws.errors import ErrorCode, ErrorResponse, sanitize_error
from werewolf_dm.interfaces.http_ws.models import HealthResponse
from werewolf_dm.interfaces.http_ws.rooms import router as rooms_router
from werewolf_dm.interfaces.http_ws.runtime import build_production_registry
from werewolf_dm.interfaces.http_ws.ws import router as ws_router


def _status_for_error_code(code: ErrorCode) -> int:
    if code is ErrorCode.ROOM_NOT_FOUND:
        return 404
    if code in {ErrorCode.TOKEN_INVALID, ErrorCode.TOKEN_EXPIRED}:
        return 401
    if code is ErrorCode.ROOM_FULL:
        return 409
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
    if owns_registry:
        app.state.room_registry = build_production_registry()
    try:
        yield
    finally:
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
) -> FastAPI:
    app = FastAPI(title="Werewolf DM S2", version="0.2.0", lifespan=lifespan)
    app.state.room_registry = registry
    app.state.token_ttl = token_ttl
    app.include_router(rooms_router)
    app.include_router(ws_router)

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
