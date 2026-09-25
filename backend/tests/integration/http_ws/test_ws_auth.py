from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.interfaces.http_ws import ws as ws_module
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.models import (
    AuthMessage,
    AuthRequiredMessage,
)
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink
from werewolf_dm.interfaces.http_ws.ws import message_loop


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set(self, now: datetime) -> None:
        self.now = now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class RecordingSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, object]] = []
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict[str, object]) -> None:
        self.sent.append(data)

    async def close(self, code: int) -> None:
        self.close_codes.append(code)

    async def receive_json(self) -> dict[str, object]:
        raise TimeoutError


def make_registry(
    *,
    tokens: tuple[str, ...] = ("host", "seat-1"),
    room_codes: tuple[str, ...] = ("ROOM01",),
) -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(tokens=tokens, room_codes=room_codes),
        seed_source=lambda: 101,
    )


def test_websocket_requires_auth_before_session_ready() -> None:
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()

        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json() == {"type": "auth.required"}
            socket.send_json(
                {
                    "type": "auth",
                    "token": joined["seat_token"],
                    "last_seq": 0,
                }
            )
            ready = socket.receive_json()

        assert ready["type"] == "session.ready"
        assert ready["snapshot"]["seat_view"]["seat_id"] == 1
        assert ready["snapshot"]["host_control"] is None


def test_host_token_receives_host_control_snapshot() -> None:
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()

        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_json(
                {
                    "type": "auth",
                    "token": created["host_token"],
                    "last_seq": 0,
                }
            )
            ready = socket.receive_json()

        assert ready["snapshot"]["seat_view"] is None
        assert ready["snapshot"]["host_control"] is not None


def test_websocket_rejects_invalid_token_with_4001() -> None:
    registry = make_registry(tokens=("host",))
    with TestClient(create_app(registry)) as client:
        client.post("/rooms", json={"display_name": "Host"})

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws") as socket,
        ):
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_json({"type": "auth", "token": "bad", "last_seq": 0})
            socket.receive_json()

    assert exc.value.code == 4001
    assert registry.auth_failures == 1


def test_websocket_malformed_auth_closes_4001_and_stops_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[ConnectionSink] = []

    class TrackingSink(ConnectionSink):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(ws_module, "ConnectionSink", TrackingSink)
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        client.post("/rooms", json={"display_name": "Host"})

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws") as socket,
        ):
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_text("{not-json")
            socket.receive_json()

    assert exc.value.code == 4001
    assert registry.auth_failures == 1
    assert len(created) == 1
    assert created[0].close_code == 4001
    assert created[0]._writer_task is not None
    assert created[0]._writer_task.done()


def test_websocket_binary_auth_closes_4001() -> None:
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        client.post("/rooms", json={"display_name": "Host"})

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws") as socket,
        ):
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_bytes(b"not-json")
            socket.receive_json()

    assert exc.value.code == 4001
    assert registry.auth_failures == 1


def test_websocket_auth_timeout_closes_with_4002(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_module, "AUTH_TIMEOUT_SECONDS", 0.0)
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        client.post("/rooms", json={"display_name": "Host"})

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws") as socket,
        ):
            assert socket.receive_json()["type"] == "auth.required"
            socket.receive_json()

    assert exc.value.code == 4002
    assert registry.auth_failures == 1


def test_query_token_does_not_replace_first_frame_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_module, "AUTH_TIMEOUT_SECONDS", 0.0)
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(f"/ws?token={created['host_token']}") as socket,
        ):
            assert socket.receive_json() == {"type": "auth.required"}
            socket.receive_json()

    assert exc.value.code == 4002


def test_auth_message_resets_idle_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[ConnectionSink] = []

    class TrackingSink(ConnectionSink):
        activity_records = 0

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

        def record_client_activity(self) -> None:
            self.activity_records += 1
            super().record_client_activity()

    monkeypatch.setattr(ws_module, "ConnectionSink", TrackingSink)
    registry = make_registry()
    with TestClient(create_app(registry)) as client:
        created_room = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created_room['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()

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
            assert created[0].activity_records == 1


def test_auth_message_is_strict() -> None:
    message = AuthMessage.model_validate({"type": "auth", "token": "opaque", "last_seq": 0})
    assert message.token == "opaque"

    with pytest.raises(ValueError):
        AuthMessage.model_validate({"type": "auth", "token": "", "last_seq": 0})


@pytest.mark.asyncio
async def test_connection_sink_has_bounded_queue_and_one_writer() -> None:
    socket = RecordingSocket()
    sink = ConnectionSink(
        socket,
        clock=MutableClock(datetime(2026, 9, 24, tzinfo=UTC)),
    )
    await sink.start()

    assert sink.send_queue.maxsize == 64
    assert sink.offer(AuthRequiredMessage())
    await sink.drain()

    assert socket.accepted is True
    assert socket.sent == [{"type": "auth.required"}]
    await sink.close(1000)


@pytest.mark.asyncio
async def test_connection_sink_closes_slow_connection_with_1013() -> None:
    sink = ConnectionSink(
        RecordingSocket(),
        clock=MutableClock(datetime(2026, 9, 24, tzinfo=UTC)),
    )
    message = AuthRequiredMessage()

    for _ in range(64):
        assert sink.offer(message)
    assert sink.offer(message) is False
    assert sink.offer(message) is False
    assert sink.offer(message) is False

    assert sink.close_code == 1013
    await sink.wait_closed()


@pytest.mark.asyncio
async def test_message_loop_closes_after_idle_timeout_with_fake_clock() -> None:
    clock = MutableClock(datetime(2026, 9, 24, tzinfo=UTC))
    socket = RecordingSocket()
    sink = ConnectionSink(socket, clock=clock)
    await sink.start()
    clock.advance(60)

    await message_loop(socket, sink, idle_timeout=60.0)

    assert socket.close_codes == [1001]
    await sink.wait_closed()
