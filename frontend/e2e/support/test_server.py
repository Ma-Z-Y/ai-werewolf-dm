import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from werewolf_dm.application.rooms import RoomRegistry, TokenService
from werewolf_dm.infrastructure.persistence import SQLiteRoomStore
from werewolf_dm.interfaces.http_ws.app import create_app

_original_socket_connect = socket.socket.connect
_network_violations: list[str] = []


def _guard_outbound_connection(
    sock: socket.socket,
    address: object,
) -> object:
    host = ""
    if isinstance(address, tuple) and address:
        host = str(address[0])
    if host not in {"127.0.0.1", "::1", "localhost"}:
        _network_violations.append(f"{host}:{address!r}")
        raise OSError("E2E_EXTERNAL_NETWORK_BLOCKED")
    return _original_socket_connect(sock, address)


socket.socket.connect = _guard_outbound_connection


class TestClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


class ResettableTokenSource:
    def __init__(
        self,
        tokens: tuple[str, ...],
        room_codes: tuple[str, ...],
    ) -> None:
        self._tokens = tokens
        self._room_codes = room_codes
        self.reset()

    def reset(self) -> None:
        self._token_index = 0
        self._room_index = 0

    def token(self) -> str:
        value = self._tokens[self._token_index]
        self._token_index += 1
        return value

    def room_code(self) -> str:
        value = self._room_codes[self._room_index]
        self._room_index += 1
        return value

    def skip_tokens(self, count: int) -> None:
        self._token_index = max(self._token_index, count)

    def skip_room_codes(self, count: int) -> None:
        self._room_index = max(self._room_index, count)


class DeterministicUuidSource:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._counter = 0

    def __call__(self) -> UUID:
        self._counter += 1
        return uuid5(NAMESPACE_URL, f"s4-e2e-{self._counter}")


class AdvanceRequest(BaseModel):
    seconds: float = Field(gt=0, le=3600)


def build_test_control_router(
    room_registry: RoomRegistry,
    test_clock: TestClock,
    expected_control_token: str,
    source: ResettableTokenSource,
    deterministic_uuid: DeterministicUuidSource,
    database_path: Path,
) -> APIRouter:
    router = APIRouter()

    async def reset_registry() -> None:
        for room_code in tuple(room_registry.rooms):
            await room_registry.remove_room(room_code)
        store = room_registry.store
        if store is not None:
            store.close()
        database_path.unlink(missing_ok=True)
        store = SQLiteRoomStore(database_path)
        store.migrate()
        room_registry.store = store
        test_clock.set(datetime(2099, 1, 1, tzinfo=UTC))
        source.reset()
        deterministic_uuid.reset()
        _network_violations.clear()
        room_registry.tokens = TokenService(
            source,
            test_clock,
            uuid_source=deterministic_uuid,
        )
        room_registry.active_connections = 0
        room_registry.auth_failures = 0
        room_registry.slow_connection_closes = 0
        room_registry._display_pairings.clear()
        room_registry._pairing_attempts.clear()

    @router.post("/__test__/reset")
    async def reset_e2e_state(request: Request) -> dict[str, str]:
        if request.headers.get("X-Test-Control") != expected_control_token:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        await reset_registry()
        return {"status": "ok"}

    @router.get("/__test__/network-state")
    async def network_state(request: Request) -> dict[str, list[str]]:
        if request.headers.get("X-Test-Control") != expected_control_token:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        return {"violations": list(_network_violations)}

    @router.get("/__test__/runtime")
    async def runtime_state(request: Request) -> dict[str, str | int]:
        if request.headers.get("X-Test-Control") != expected_control_token:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        return {
            "database_path": str(database_path),
            "process_id": os.getpid(),
        }

    @router.post("/__test__/restart")
    async def restart_app(request: Request) -> dict[str, str]:
        if request.headers.get("X-Test-Control") != expected_control_token:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        server = getattr(request.app.state, "test_server", None)
        if server is None:
            raise HTTPException(status_code=503, detail="SERVER_NOT_READY")

        async def stop_after_response() -> None:
            await asyncio.sleep(0.05)
            server.should_exit = True

        request.app.state.test_restart_task = asyncio.create_task(stop_after_response())
        return {
            "database_path": str(database_path),
            "status": "restarting",
        }

    @router.post("/__test__/rooms/{room_code}/advance")
    async def advance_room(
        room_code: str,
        body: AdvanceRequest,
        request: Request,
    ) -> dict[str, str]:
        if request.headers.get("X-Test-Control") != expected_control_token:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        room = room_registry.get_by_code(room_code)
        test_clock.advance(seconds=body.seconds)
        deadline_at = room.core.state.deadline_at
        if deadline_at is not None:
            await room.enqueue_timer_tick(
                revision=room.core.state.revision,
                deadline_at=deadline_at,
                now=test_clock(),
            )
        return {"status": "ok"}

    return router


def build_app(
    database_path: Path,
    control_token: str,
) -> tuple[FastAPI, RoomRegistry]:
    store = SQLiteRoomStore(database_path)
    store.migrate()
    clock = TestClock(datetime(2099, 1, 1, tzinfo=UTC))
    source = ResettableTokenSource(
        tokens=tuple(f"token-{index}" for index in range(500)),
        room_codes=tuple(f"ROOM{index:02d}" for index in range(100)),
    )
    deterministic_uuid = DeterministicUuidSource()
    registry = RoomRegistry(
        clock=clock,
        token_source=source,
        seed_source=lambda: 1001,
        uuid_source=deterministic_uuid,
        store=store,
    )
    source.skip_tokens(len(store.load_tokens()))
    source.skip_room_codes(len(store.load_rooms()))
    app = create_app(registry=registry)
    app.include_router(
        build_test_control_router(
            registry,
            clock,
            control_token,
            source,
            deterministic_uuid,
            database_path,
        )
    )
    return app, registry


async def run_child(database_path: Path, control_token: str) -> None:
    app, registry = build_app(database_path, control_token)
    await registry.start_rooms()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    app.state.test_server = server
    try:
        await server.serve()
    finally:
        await registry.close(close_store=True)


def run_supervisor(control_token: str) -> None:
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="werewolf-dm-e2e-") as temporary:
        database_path = Path(temporary) / "e2e.sqlite3"
        child: subprocess.Popen[bytes] | None = None
        try:
            while True:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        str(script),
                        "--child",
                        str(database_path),
                    ],
                    env={**os.environ, "E2E_CONTROL_TOKEN": control_token},
                )
                return_code = child.wait()
                if return_code != 0:
                    raise SystemExit(return_code)
                time.sleep(0.1)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=10)


def main() -> None:
    control_token = os.environ["E2E_CONTROL_TOKEN"]
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        asyncio.run(run_child(Path(sys.argv[2]), control_token))
        return
    run_supervisor(control_token)


if __name__ == "__main__":
    main()
