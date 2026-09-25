from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.domain.contracts import (
    CommandEnvelope,
    CommandErrorCode,
    HostPauseCommand,
    JoinRoomCommand,
    SetReadyCommand,
    SpeakCommand,
)
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink
from werewolf_dm.interfaces.http_ws.ws import message_loop


class StopLoop(Exception):
    pass


class ScriptedSocket:
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.sent: list[dict[str, object]] = []
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict[str, object]) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)

    async def receive_json(self) -> object:
        if self.messages:
            return self.messages.pop(0)
        raise StopLoop


def make_registry() -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host", "seat-1"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )


def command_message(
    room_id: UUID,
    payload: JoinRoomCommand | SetReadyCommand | SpeakCommand | HostPauseCommand,
    *,
    command_id: UUID,
    expected_revision: int,
    actor: dict[str, object] | None = None,
) -> dict[str, object]:
    envelope = CommandEnvelope(
        command_id=command_id,
        room_id=room_id,
        expected_revision=expected_revision,
        issued_at=datetime(2026, 9, 24, tzinfo=UTC),
        payload=payload,
    )
    message: dict[str, object] = {
        "type": "command",
        "command": envelope.model_dump(mode="json"),
    }
    if actor is not None:
        message["actor"] = actor
    return message


async def run_scripted_message_loop(
    socket: ScriptedSocket,
    sink: ConnectionSink,
    registry: RoomRegistry,
    room_code: str,
    seat_token: str,
) -> None:
    record = registry.tokens.resolve(seat_token)
    actor = registry.actor_for(record)
    sink.bind_actor(actor_type="seat", seat_id=record.seat_id)
    await sink.start()
    try:
        with pytest.raises(StopLoop):
            await message_loop(
                socket,
                sink,
                room=registry.get_by_code(room_code),
                record=record,
                actor=actor,
                idle_timeout=60.0,
            )
        await sink.drain()
    finally:
        await sink.close(1000)
        await registry.remove_room(room_code)


@pytest.mark.asyncio
async def test_legal_command_returns_exact_command_ack() -> None:
    registry = make_registry()
    created = registry.create_room(ttl=timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    await registry.start_room(created.room_code)
    command_id = uuid4()
    socket = ScriptedSocket(
        [
            command_message(
                created.room_id,
                JoinRoomCommand(seat_id=1, display_name="Alice"),
                command_id=command_id,
                expected_revision=0,
            )
        ]
    )
    sink = ConnectionSink(socket, clock=registry.clock)

    await run_scripted_message_loop(
        socket,
        sink,
        registry,
        created.room_code,
        joined.seat_token,
    )

    ack = socket.sent[-1]
    assert ack == {
        "type": "command.ack",
        "command_id": str(command_id),
        "accepted": True,
        "revision": 1,
        "error_code": None,
        "outbox_seq": 1,
    }
    assert "event_ids" not in ack
    assert registry.rooms == {}


@pytest.mark.asyncio
async def test_illegal_command_returns_s1_error_code_without_advancing_outbox() -> None:
    registry = make_registry()
    created = registry.create_room(ttl=timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    await registry.start_room(created.room_code)
    command_id = uuid4()
    socket = ScriptedSocket(
        [
            command_message(
                created.room_id,
                SpeakCommand(text="not my turn"),
                command_id=command_id,
                expected_revision=0,
            )
        ]
    )
    sink = ConnectionSink(socket, clock=registry.clock)

    await run_scripted_message_loop(
        socket,
        sink,
        registry,
        created.room_code,
        joined.seat_token,
    )

    assert socket.sent == [
        {
            "type": "command.ack",
            "command_id": str(command_id),
            "accepted": False,
            "revision": 0,
            "error_code": "ILLEGAL_PHASE",
            "outbox_seq": 0,
        }
    ]
    assert CommandErrorCode(socket.sent[0]["error_code"]) is CommandErrorCode.ILLEGAL_PHASE
    assert "event_ids" not in socket.sent[0]


@pytest.mark.asyncio
async def test_non_object_json_does_not_interrupt_message_loop() -> None:
    registry = make_registry()
    created = registry.create_room(ttl=timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    await registry.start_room(created.room_code)
    command_id = uuid4()
    socket = ScriptedSocket(
        [
            [],
            None,
            "not-an-object",
            command_message(
                created.room_id,
                JoinRoomCommand(seat_id=1, display_name="Alice"),
                command_id=command_id,
                expected_revision=0,
            ),
        ]
    )
    sink = ConnectionSink(socket, clock=registry.clock)

    await run_scripted_message_loop(
        socket,
        sink,
        registry,
        created.room_code,
        joined.seat_token,
    )

    acks = [message for message in socket.sent if message["type"] == "command.ack"]
    assert len(acks) == 1
    assert acks[0]["command_id"] == str(command_id)
    assert acks[0]["accepted"] is True


@pytest.mark.asyncio
async def test_stale_revision_returns_conflict_without_advancing_outbox() -> None:
    registry = make_registry()
    created = registry.create_room(ttl=timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    await registry.start_room(created.room_code)
    accepted_id = uuid4()
    rejected_id = uuid4()
    socket = ScriptedSocket(
        [
            command_message(
                created.room_id,
                JoinRoomCommand(seat_id=1, display_name="Alice"),
                command_id=accepted_id,
                expected_revision=0,
            ),
            command_message(
                created.room_id,
                SetReadyCommand(ready=True),
                command_id=rejected_id,
                expected_revision=0,
            ),
        ]
    )
    sink = ConnectionSink(socket, clock=registry.clock)

    await run_scripted_message_loop(
        socket,
        sink,
        registry,
        created.room_code,
        joined.seat_token,
    )

    acks = [message for message in socket.sent if message["type"] == "command.ack"]
    assert [ack["command_id"] for ack in acks] == [str(accepted_id), str(rejected_id)]
    assert acks[1] == {
        "type": "command.ack",
        "command_id": str(rejected_id),
        "accepted": False,
        "revision": 1,
        "error_code": "REVISION_CONFLICT",
        "outbox_seq": 1,
    }


def test_client_actor_field_cannot_override_token_binding() -> None:
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()
        room_id = UUID(created["room_id"])
        spoofed_id = uuid4()
        accepted_id = uuid4()

        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_json(
                {
                    "type": "auth",
                    "token": joined["seat_token"],
                    "last_seq": 0,
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_json(
                command_message(
                    room_id,
                    HostPauseCommand(reason="spoofed host"),
                    command_id=spoofed_id,
                    expected_revision=0,
                    actor={
                        "actor_type": "host",
                        "seat_id": None,
                        "room_id": str(room_id),
                    },
                )
            )
            socket.send_json(
                command_message(
                    room_id,
                    JoinRoomCommand(seat_id=1, display_name="Alice"),
                    command_id=accepted_id,
                    expected_revision=0,
                )
            )
            for _ in range(3):
                socket.send_json({"type": "subscribe", "channel": "public"})
            sent = [socket.receive_json() for _ in range(4)]

    acks = [message for message in sent if message["type"] == "command.ack"]
    assert len(acks) == 1
    assert acks[0]["command_id"] == str(accepted_id)
    assert acks[0]["accepted"] is True
    assert acks[0]["revision"] == 1
    room = registry.rooms_by_id()[room_id]
    assert room.core.state.paused is False


def test_duplicate_command_after_state_advance_does_not_rebroadcast() -> None:
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()
        room_id = UUID(created["room_id"])
        first_id = uuid4()
        second_id = uuid4()

        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_json(
                {
                    "type": "auth",
                    "token": joined["seat_token"],
                    "last_seq": 0,
                }
            )
            ready = socket.receive_json()
            baseline_seq = ready["snapshot"]["outbox_seq"]

            socket.send_json(
                command_message(
                    room_id,
                    JoinRoomCommand(seat_id=1, display_name="Alice"),
                    command_id=first_id,
                    expected_revision=0,
                )
            )
            first_messages = [socket.receive_json() for _ in range(3)]
            assert [message["type"] for message in first_messages] == [
                "public.view.updated",
                "seat.view.updated",
                "command.ack",
            ]
            assert [message["outbox_seq"] for message in first_messages] == [
                baseline_seq + 1,
                baseline_seq + 1,
                baseline_seq + 1,
            ]
            assert first_messages[-1]["accepted"] is True
            assert first_messages[-1]["revision"] == 1

            socket.send_json(
                command_message(
                    room_id,
                    SetReadyCommand(ready=True),
                    command_id=second_id,
                    expected_revision=1,
                )
            )
            second_messages = [socket.receive_json() for _ in range(3)]
            assert [message["type"] for message in second_messages] == [
                "public.view.updated",
                "seat.view.updated",
                "command.ack",
            ]
            assert [message["outbox_seq"] for message in second_messages] == [
                baseline_seq + 2,
                baseline_seq + 2,
                baseline_seq + 2,
            ]
            assert second_messages[-1]["accepted"] is True
            assert second_messages[-1]["revision"] == 2

            socket.send_json(
                command_message(
                    room_id,
                    JoinRoomCommand(seat_id=1, display_name="Alice"),
                    command_id=first_id,
                    expected_revision=0,
                )
            )
            duplicate_ack = socket.receive_json()
            assert duplicate_ack == {
                "type": "command.ack",
                "command_id": str(first_id),
                "accepted": True,
                "revision": 1,
                "error_code": None,
                "outbox_seq": baseline_seq + 2,
            }

            socket.send_json({"type": "subscribe", "channel": "public"})
            trailing = [socket.receive_json() for _ in range(2)]
            assert [message["type"] for message in trailing] == [
                "public.view.updated",
                "seat.view.updated",
            ]
            assert [message["outbox_seq"] for message in trailing] == [
                baseline_seq + 2,
                baseline_seq + 2,
            ]
