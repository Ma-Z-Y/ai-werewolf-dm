import os
import socket
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import uvicorn
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from werewolf_dm.application.dm_contracts import (
    DMTemplateMessage,
    TemplateAudience,
)
from werewolf_dm.application.rooms import (
    DMTemplateMessageUpdate,
    RoomRegistry,
    TokenService,
)
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


class DeterministicUuidSource:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._counter = 0

    def __call__(self) -> UUID:
        self._counter += 1
        return uuid5(NAMESPACE_URL, f"s4-e2e-{self._counter}")


control_token = os.environ["E2E_CONTROL_TOKEN"]
clock = TestClock(datetime(2099, 1, 1, tzinfo=UTC))
source = ResettableTokenSource(
    tokens=tuple(f"token-{index}" for index in range(200)),
    room_codes=tuple(f"ROOM{index:02d}" for index in range(10)),
)
deterministic_uuid = DeterministicUuidSource()
registry = RoomRegistry(
    clock=clock,
    token_source=source,
    seed_source=lambda: 1001,
    uuid_source=deterministic_uuid,
)


class AdvanceRequest(BaseModel):
    seconds: float = Field(gt=0, le=3600)


class SeatDmRequest(BaseModel):
    seat_id: int = Field(ge=1, le=6)


def build_test_control_router(
    room_registry: RoomRegistry,
    test_clock: TestClock,
    expected_control_token: str,
) -> APIRouter:
    router = APIRouter()

    async def reset_registry() -> None:
        for room_code in tuple(room_registry.rooms):
            await room_registry.remove_room(room_code)
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

    @router.post("/__test__/rooms/{room_code}/seat-dm")
    async def inject_seat_dm(
        room_code: str,
        body: SeatDmRequest,
        request: Request,
    ) -> dict[str, str | int]:
        if request.headers.get("X-Test-Control") != expected_control_token:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        room = room_registry.get_by_code(room_code)
        session_id = room.seat_session_ids.get(body.seat_id)
        if session_id is None:
            raise HTTPException(status_code=409, detail="SEAT_NOT_ATTACHED")

        transport_seq = room.outbox_seq + 1
        room.outbox_seq = transport_seq
        message = DMTemplateMessage(
            message_id=uuid5(
                NAMESPACE_URL,
                f"{room.room_id}:{room.core.state.revision}:seat:{body.seat_id}",
            ),
            room_id=room.room_id,
            revision=room.core.state.revision,
            channel="seat",
            audience_bindings=(
                TemplateAudience(
                    seat_id=body.seat_id,
                    session_id=session_id,
                ),
            ),
            text=f"请 {body.seat_id} 号玩家在 女巫行动 行动。",
            source="template",
        )
        delivered = sum(
            subscriber.offer(
                DMTemplateMessageUpdate(
                    server_time=test_clock(),
                    outbox_seq=transport_seq,
                    message=message,
                )
            )
            for subscriber in tuple(room.subscribers.values())
            if subscriber.actor_type == "seat"
            and subscriber.seat_id == body.seat_id
            and "seat" in subscriber.channels
        )
        if delivered == 0:
            raise HTTPException(status_code=409, detail="NO_TARGET_SUBSCRIBER")
        return {
            "status": "ok",
            "message_id": str(message.message_id),
            "outbox_seq": transport_seq,
        }

    return router


app = create_app(registry=registry)
app.include_router(build_test_control_router(registry, clock, control_token))

uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
