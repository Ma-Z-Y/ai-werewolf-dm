from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import (
    RoomRegistry,
    SeatViewUpdate,
    SequenceTokenSource,
)
from werewolf_dm.domain.visibility import project_seat_view
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink
from werewolf_dm.interfaces.http_ws.ws import message_loop


class StopLoop(Exception):
    pass


class ScriptedSocket:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages
        self.sent: list[dict[str, object]] = []
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict[str, object]) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)

    async def receive_json(self) -> dict[str, object]:
        if self.messages:
            return self.messages.pop(0)
        raise StopLoop


def make_registry(*, tokens: tuple[str, ...]) -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=tokens,
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )


@pytest.mark.asyncio
async def test_seat_channel_publishes_own_authenticated_projection() -> None:
    registry = make_registry(tokens=("host", "seat-1", "seat-2", "seat-3"))
    created = registry.create_room(timedelta(hours=1))
    joined = [registry.join_room(created.room_code, f"P{seat}") for seat in range(1, 4)]
    await registry.start_room(created.room_code)
    room = registry.get_by_code(created.room_code)
    record = registry.tokens.resolve(joined[2].seat_token)
    socket = ScriptedSocket([{"type": "subscribe", "channel": "seat", "seat_id": 3}])
    sink = ConnectionSink(socket, clock=registry.clock)
    sink.bind_actor(actor_type="seat", seat_id=3)
    await sink.start()

    with pytest.raises(StopLoop):
        await message_loop(
            socket,
            sink,
            room=room,
            record=record,
            idle_timeout=60.0,
        )
    await sink.drain()

    assert [message["type"] for message in socket.sent] == [
        "public.view.updated",
        "seat.view.updated",
    ]
    assert set(socket.sent[1]) == {
        "type",
        "server_time",
        "outbox_seq",
        "seat_id",
        "seat_view",
    }
    parsed = SeatViewUpdate.validate_json_payload(socket.sent[1])
    actor = registry.actor_for(record)

    assert parsed.type == "seat.view.updated"
    assert parsed.seat_id == 3
    assert parsed.seat_view.model_dump(mode="json") == project_seat_view(
        room.core.state,
        3,
        actor,
    ).model_dump(mode="json")
    await sink.close(1000)


def test_seat_3_token_cannot_subscribe_seat_5_with_channel_forbidden() -> None:
    registry = make_registry(tokens=("host", "seat-1", "seat-2", "seat-3"))
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = [
            client.post(
                f"/rooms/{created['room_code']}/join",
                json={"display_name": f"P{seat}"},
            ).json()
            for seat in range(1, 4)
        ]

        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json()["type"] == "auth.required"
            websocket.send_json(
                {
                    "type": "auth",
                    "token": joined[2]["seat_token"],
                    "last_seq": 0,
                }
            )
            ready = websocket.receive_json()
            assert ready["snapshot"]["seat_view"]["seat_id"] == 3

            websocket.send_json({"type": "subscribe", "channel": "seat", "seat_id": 5})
            websocket.send_json({"type": "subscribe", "channel": "public"})
            error = websocket.receive_json()

            assert error["type"] == "error"
            assert error["code"] == "CHANNEL_FORBIDDEN"
            assert error["message"] == "频道不可用"
            UUID(error["request_id"])

            trailing = websocket.receive_json()
            assert trailing["type"] == "public.view.updated"
            assert trailing["outbox_seq"] == ready["snapshot"]["outbox_seq"]


def test_auth_actor_spoof_is_rejected_and_cannot_override_token_seat() -> None:
    registry = make_registry(tokens=("host", "seat-1"))
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws") as websocket,
        ):
            assert websocket.receive_json()["type"] == "auth.required"
            websocket.send_json(
                {
                    "type": "auth",
                    "token": joined["seat_token"],
                    "last_seq": 0,
                    "actor": {
                        "actor_type": "host",
                        "seat_id": 5,
                        "room_id": created["room_id"],
                    },
                }
            )
            websocket.receive_json()

    assert exc.value.code == 4001
    assert registry.auth_failures == 1
