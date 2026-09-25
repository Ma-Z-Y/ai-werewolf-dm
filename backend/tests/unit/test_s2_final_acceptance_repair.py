from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.factories import make_envelope
from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import (
    RoomActor,
    RoomRegistry,
    SequenceTokenSource,
    TokenRecord,
)
from werewolf_dm.domain.contracts import JoinRoomCommand
from werewolf_dm.interfaces.http_ws import app as app_module
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.errors import (
    ConnectionRateLimiter,
    sanitize_error,
)
from werewolf_dm.interfaces.http_ws.metrics import LatencyRecorder
from werewolf_dm.interfaces.http_ws.models import AuthRequiredMessage
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink, reap_periodically
from werewolf_dm.interfaces.http_ws.ws import _send_rate_limited, message_loop


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set(self, now: datetime) -> None:
        self.now = now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class BlockingSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, object]] = []
        self.close_codes: list[int] = []
        self.release = asyncio.Event()
        self.send_started = asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict[str, object]) -> None:
        self.send_started.set()
        await self.release.wait()
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)


class OSErrorSocket(BlockingSocket):
    async def send_json(self, data: dict[str, object]) -> None:
        del data
        raise OSError("CLIENT_DISCONNECTED")


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
        raise RuntimeError("SCRIPT_EXHAUSTED")


class IdleProtocolSocket:
    def __init__(self, clock: MutableClock, message: object) -> None:
        self.clock = clock
        self.message = message
        self.calls = 0
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict[str, object]) -> None:
        del data

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)

    async def receive_json(self) -> object:
        self.calls += 1
        if self.calls == 1:
            self.clock.advance(9)
            return self.message
        if self.calls == 2:
            self.clock.advance(2)
            raise TimeoutError
        raise WebSocketDisconnect(1000)


class TokenExpirySocket:
    def __init__(self, clock: MutableClock, *, timeout: bool) -> None:
        self.clock = clock
        self.timeout = timeout
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict[str, object]) -> None:
        del data

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)

    async def receive_json(self) -> object:
        self.clock.advance(11)
        if self.timeout:
            raise TimeoutError
        return {"type": "ping"}


class FailingTokenSource:
    def __init__(self) -> None:
        self.room_codes = iter(("ROOM01", "ROOM02"))

    def token(self) -> str:
        raise RuntimeError("TOKEN_SOURCE_FAILED")

    def room_code(self) -> str:
        return next(self.room_codes)


class FakeSubscriber:
    def __init__(self, *, actor_type: str = "seat", seat_id: int | None = 1) -> None:
        self.subscription_id = uuid4()
        self.actor_type = actor_type
        self.seat_id = seat_id
        self.session_id = None
        self.channels = frozenset({"public", "seat"})
        self.close_codes: list[int] = []

    def offer(self, message: object) -> bool:
        del message
        return True

    def request_close(self, code: int) -> None:
        self.close_codes.append(code)


def make_registry(
    *,
    clock: MutableClock | FrozenClock | None = None,
    tokens: tuple[str, ...] = ("host", "seat-1"),
) -> RoomRegistry:
    return RoomRegistry(
        clock=clock or FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)),
        token_source=SequenceTokenSource(tokens=tokens, room_codes=("ROOM01",)),
        seed_source=lambda: 101,
    )


@pytest.mark.asyncio
async def test_writer_oserror_closes_without_escaping_or_leaking_queue_tasks() -> None:
    socket = OSErrorSocket()
    sink = ConnectionSink(socket, clock=FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)))
    await sink.start()
    assert sink.offer(AuthRequiredMessage())

    await asyncio.wait_for(sink.wait_closed(), timeout=0.5)

    assert sink.close_code == 1011
    assert socket.close_codes == [1011]
    assert sink.send_queue._unfinished_tasks == 0


@pytest.mark.asyncio
async def test_full_queue_close_allows_drain_to_finish() -> None:
    socket = BlockingSocket()
    sink = ConnectionSink(
        socket,
        clock=FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)),
        queue_size=1,
    )
    await sink.start()
    message = AuthRequiredMessage()

    for _ in range(4):
        sink.offer(message)

    assert sink.close_code == 1013
    await asyncio.wait_for(sink.drain(), timeout=0.5)
    assert socket.close_codes == [1013]
    assert sink.send_queue._unfinished_tasks == 0


@pytest.mark.asyncio
async def test_full_queue_grace_closes_without_further_offers() -> None:
    socket = BlockingSocket()
    sink = ConnectionSink(
        socket,
        clock=FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)),
        queue_size=1,
    )
    sink.FULL_QUEUE_GRACE_SECONDS = 0.01
    await sink.start()
    message = AuthRequiredMessage()

    assert sink.offer(message)
    await socket.send_started.wait()
    assert sink.offer(message)
    assert sink.offer(message) is False

    await asyncio.wait_for(sink.wait_closed(), timeout=0.5)

    assert sink.close_code == 1013
    assert socket.close_codes == [1013]


@pytest.mark.asyncio
async def test_rate_limit_close_does_not_hang_on_slow_writer() -> None:
    clock = FrozenClock(datetime(2026, 9, 25, tzinfo=UTC))
    socket = BlockingSocket()
    sink = ConnectionSink(socket, clock=clock, queue_size=1)
    limiter = ConnectionRateLimiter(clock, strike_limit=1)
    await sink.start()
    message = AuthRequiredMessage()
    for _ in range(4):
        sink.offer(message)

    assert await asyncio.wait_for(_send_rate_limited(sink, limiter), timeout=0.5)
    assert socket.close_codes[-1] in {1008, 1013}


@pytest.mark.asyncio
async def test_rate_limit_close_times_out_when_slow_writer_has_spare_queue() -> None:
    clock = FrozenClock(datetime(2026, 9, 25, tzinfo=UTC))
    socket = BlockingSocket()
    sink = ConnectionSink(socket, clock=clock)
    limiter = ConnectionRateLimiter(clock, strike_limit=1)
    await sink.start()

    assert await asyncio.wait_for(_send_rate_limited(sink, limiter), timeout=0.5)

    assert socket.close_codes == [1008]
    assert sink.close_code == 1008


@pytest.mark.asyncio
async def test_authenticated_message_loop_closes_when_token_expires() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    record = TokenRecord(
        token_digest="digest",
        room_id=UUID(int=1),
        actor_type="seat",
        seat_id=1,
        issued_at=clock(),
        expires_at=clock() + timedelta(seconds=10),
    )
    socket = ScriptedSocket([{"type": "ping"}])
    sink = ConnectionSink(socket, clock=clock)
    await sink.start()
    clock.advance(11)

    await asyncio.wait_for(
        message_loop(socket, sink, record=record, idle_timeout=60.0),
        timeout=0.5,
    )

    assert socket.close_codes == [4001]


@pytest.mark.asyncio
async def test_token_expires_while_waiting_for_receive() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    record = TokenRecord(
        token_digest="digest",
        room_id=UUID(int=1),
        actor_type="seat",
        seat_id=1,
        issued_at=clock(),
        expires_at=clock() + timedelta(seconds=10),
    )
    socket = TokenExpirySocket(clock, timeout=True)
    sink = ConnectionSink(socket, clock=clock)
    await sink.start()

    await asyncio.wait_for(
        message_loop(socket, sink, record=record, idle_timeout=60.0),
        timeout=0.5,
    )

    assert socket.close_codes == [4001]


@pytest.mark.asyncio
async def test_token_expires_after_message_is_received() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    record = TokenRecord(
        token_digest="digest",
        room_id=UUID(int=1),
        actor_type="seat",
        seat_id=1,
        issued_at=clock(),
        expires_at=clock() + timedelta(seconds=10),
    )
    socket = TokenExpirySocket(clock, timeout=False)
    sink = ConnectionSink(socket, clock=clock)
    await sink.start()

    await asyncio.wait_for(
        message_loop(socket, sink, record=record, idle_timeout=60.0),
        timeout=0.5,
    )

    assert socket.close_codes == [4001]


@pytest.mark.asyncio
async def test_room_stop_requests_subscriber_close_and_loop_rejects_closed_room() -> None:
    now = datetime(2026, 9, 25, tzinfo=UTC)
    actor = RoomActor(
        room_id=UUID(int=1),
        room_code="ROOM01",
        seed=101,
        clock=FrozenClock(now),
        expires_at=now + timedelta(hours=1),
        last_activity_at=now,
    )
    subscriber = FakeSubscriber()
    await actor.start()
    await actor.attach_subscriber(subscriber)
    await actor.stop()

    assert subscriber.close_codes == [4001]

    socket = ScriptedSocket([{"type": "ping"}])
    sink = ConnectionSink(socket, clock=actor.clock)
    await sink.start()
    await asyncio.wait_for(
        message_loop(socket, sink, room=actor, idle_timeout=60.0),
        timeout=0.5,
    )

    assert socket.close_codes == [4001]


@pytest.mark.asyncio
async def test_message_loop_rejects_room_expired_during_session() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    actor = RoomActor(
        room_id=UUID(int=1),
        room_code="ROOM01",
        seed=101,
        clock=clock,
        expires_at=clock() + timedelta(seconds=10),
        last_activity_at=clock(),
    )
    socket = ScriptedSocket([{"type": "ping"}])
    sink = ConnectionSink(socket, clock=clock)
    await actor.start()
    await sink.start()
    clock.advance(11)

    try:
        await asyncio.wait_for(
            message_loop(socket, sink, room=actor, idle_timeout=60.0),
            timeout=0.5,
        )
    finally:
        await actor.stop()

    assert socket.close_codes == [4001]


@pytest.mark.asyncio
async def test_same_actor_attach_replaces_old_subscriber() -> None:
    now = datetime(2026, 9, 25, tzinfo=UTC)
    actor = RoomActor(
        room_id=UUID(int=1),
        room_code="ROOM01",
        seed=101,
        clock=FrozenClock(now),
        expires_at=now + timedelta(hours=1),
        last_activity_at=now,
    )
    old = FakeSubscriber()
    new = FakeSubscriber()
    await actor.start()
    try:
        await actor.attach_subscriber(old)
        await actor.attach_subscriber(new)

        assert old.close_codes == [4003]
        assert tuple(actor.subscribers.values()) == (new,)
    finally:
        await actor.stop()


@pytest.mark.asyncio
async def test_same_actor_attach_replaces_real_connection_sinks() -> None:
    now = datetime(2026, 9, 25, tzinfo=UTC)
    clock = FrozenClock(now)
    actor = RoomActor(
        room_id=UUID(int=1),
        room_code="ROOM01",
        seed=101,
        clock=clock,
        expires_at=now + timedelta(hours=1),
        last_activity_at=now,
    )
    old_socket = ScriptedSocket([])
    new_socket = ScriptedSocket([])
    old_sink = ConnectionSink(old_socket, clock=clock)
    new_sink = ConnectionSink(new_socket, clock=clock)
    await actor.start()
    await old_sink.start()
    await new_sink.start()
    old_sink.bind_actor(actor_type="seat", seat_id=1)
    new_sink.bind_actor(actor_type="seat", seat_id=1)

    try:
        await actor.attach_subscriber(old_sink)
        await actor.attach_subscriber(new_sink)
        await asyncio.wait_for(old_sink.wait_closed(), timeout=0.5)

        assert old_sink.close_code == 4003
        assert old_socket.close_codes == [4003]
        assert new_sink.close_code is None
        assert tuple(actor.subscribers.values()) == (new_sink,)
    finally:
        await new_sink.close(1000)
        await actor.stop()


@pytest.mark.asyncio
async def test_submit_before_room_start_is_rejected() -> None:
    clock = FrozenClock(datetime(2026, 9, 25, tzinfo=UTC))
    registry = make_registry(clock=clock)
    created = registry.create_room(timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    record = registry.tokens.resolve(joined.seat_token)
    actor = registry.actor_for(record)
    room = registry.get_by_code(created.room_code)
    envelope = make_envelope(
        actor,
        JoinRoomCommand(seat_id=1, display_name="Alice"),
        expected_revision=0,
        now=clock(),
    )

    with pytest.raises(RuntimeError, match="ROOM_NOT_STARTED"):
        await asyncio.wait_for(room.submit_command(envelope, actor), timeout=0.5)


def test_expired_room_cannot_be_looked_up_or_joined() -> None:
    clock = FrozenClock(datetime(2026, 9, 25, tzinfo=UTC))
    registry = make_registry(clock=clock, tokens=("host",))
    created = registry.create_room(timedelta(hours=1))
    clock.set(created.expires_at)

    with pytest.raises(ValueError, match="ROOM_NOT_FOUND"):
        registry.get_by_code(created.room_code)
    with pytest.raises(ValueError, match="ROOM_NOT_FOUND"):
        registry.join_room(created.room_code, "Late")


def test_failed_host_token_issue_does_not_publish_room() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)),
        token_source=FailingTokenSource(),
        seed_source=lambda: 101,
    )

    with pytest.raises(RuntimeError, match="TOKEN_SOURCE_FAILED"):
        registry.create_room(timedelta(hours=1))

    assert registry.rooms == {}


@pytest.mark.asyncio
async def test_reaper_continues_after_cancelled_room_actor() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(
            tokens=("host-1", "host-2"),
            room_codes=("ROOM01", "ROOM02"),
        ),
        seed_source=lambda: 101,
    )
    first = registry.create_room(timedelta(hours=1))
    second = registry.create_room(timedelta(hours=1))
    await registry.start_room(first.room_code)
    await registry.start_room(second.room_code)
    first_actor = registry.get_by_code(first.room_code)
    assert first_actor._task is not None
    first_actor._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_actor._task
    clock.set(first.expires_at)

    assert await registry.reap_expired() == 2
    assert registry.rooms == {}


def test_expired_room_is_not_returned_by_id_before_reaping() -> None:
    clock = FrozenClock(datetime(2026, 9, 25, tzinfo=UTC))
    registry = make_registry(clock=clock, tokens=("host",))
    created = registry.create_room(timedelta(hours=1))
    clock.set(created.expires_at)

    assert registry.rooms_by_id() == {}


def test_websocket_rejects_expired_room_while_seat_token_is_valid() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    registry = make_registry(clock=clock, tokens=("host", "seat"))
    app = create_app(registry=registry)

    with TestClient(app) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        clock.advance(5 * 60 * 60)
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()
        clock.advance(60 * 60)

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws") as websocket,
        ):
            assert websocket.receive_json() == {"type": "auth.required"}
            websocket.send_json({"type": "auth", "token": joined["seat_token"]})
            websocket.receive_json()

    assert exc.value.code == 4001


def test_registry_enforces_room_and_connection_limits() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host-1", "host-2"),
            room_codes=("ROOM01", "ROOM02"),
        ),
        seed_source=lambda: 101,
        max_rooms=1,
        max_connections=1,
    )
    registry.create_room(timedelta(hours=1))

    with pytest.raises(ValueError, match="ROOM_LIMIT_REACHED"):
        registry.create_room(timedelta(hours=1))
    assert registry.connection_opened() is True
    assert registry.connection_opened() is False


def test_app_owned_reaper_removes_expired_rooms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FrozenClock(datetime(2026, 9, 25, tzinfo=UTC))
    registry = make_registry(clock=clock, tokens=("host",))
    monkeypatch.setattr(app_module, "build_production_registry", lambda: registry)
    app = create_app(reaper_interval_seconds=0.0)

    with TestClient(app) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        clock.set(datetime.fromisoformat(created["expires_at"]))
        for _ in range(100):
            client.get("/healthz")
            if not registry.rooms:
                break
        assert registry.rooms == {}


def test_app_owned_reaper_is_cancelled_and_awaited_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = make_registry(tokens=("host",))
    cancelled: list[str] = []

    async def blocking_reaper(
        room_registry: RoomRegistry,
        *,
        interval_seconds: float,
    ) -> None:
        del room_registry, interval_seconds
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.append("cancelled")

    monkeypatch.setattr(app_module, "build_production_registry", lambda: registry)
    monkeypatch.setattr(app_module, "reap_periodically", blocking_reaper)
    app = create_app()

    with TestClient(app):
        assert cancelled == []

    assert cancelled == ["cancelled"]


@pytest.mark.asyncio
async def test_malformed_ping_still_consumes_control_rate_limit() -> None:
    clock = FrozenClock(datetime(2026, 9, 25, tzinfo=UTC))
    socket = ScriptedSocket([{"type": "ping", "unexpected": index} for index in range(12)])
    sink = ConnectionSink(socket, clock=clock)
    await sink.start()

    await asyncio.wait_for(message_loop(socket, sink, idle_timeout=60.0), timeout=0.5)

    assert socket.close_codes == [1008]


@pytest.mark.asyncio
async def test_malformed_ping_does_not_refresh_idle_activity() -> None:
    started_at = datetime(2026, 9, 25, tzinfo=UTC)
    clock = MutableClock(started_at)
    socket = IdleProtocolSocket(clock, {"type": "ping", "unexpected": True})
    sink = ConnectionSink(socket, clock=clock)
    await sink.start()

    await asyncio.wait_for(message_loop(socket, sink, idle_timeout=10.0), timeout=0.5)

    assert sink.last_client_activity_at == started_at
    assert socket.close_codes == [1001]


@pytest.mark.asyncio
async def test_invalid_protocol_message_does_not_refresh_idle_activity() -> None:
    started_at = datetime(2026, 9, 25, tzinfo=UTC)
    clock = MutableClock(started_at)
    socket = IdleProtocolSocket(clock, {"type": "not-a-protocol-message"})
    sink = ConnectionSink(socket, clock=clock)
    await sink.start()

    await asyncio.wait_for(message_loop(socket, sink, idle_timeout=10.0), timeout=0.5)

    assert sink.last_client_activity_at == started_at
    assert socket.close_codes == [1001]


@pytest.mark.asyncio
async def test_reaper_log_redacts_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENTINEL_REAPER_TOKEN"

    class FailingRegistry:
        calls = 0

        async def reap_expired(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(sentinel)
            await asyncio.Event().wait()
            return 0

    registry = FailingRegistry()
    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(
            reap_periodically(registry, interval_seconds=0.0)  # type: ignore[arg-type]
        )
        for _ in range(100):
            if "Room reaper failed" in caplog.text:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text


def test_unknown_error_logging_does_not_include_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENTINEL_RAW_TOKEN_FRAGMENT"
    with caplog.at_level(logging.ERROR):
        response = sanitize_error(RuntimeError(sentinel))

    assert response.code.value == "INTERNAL_ERROR"
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_latency_recorder_observes_bounded_queue_depth() -> None:
    recorder = LatencyRecorder(window=100)
    socket = BlockingSocket()
    sink = ConnectionSink(
        socket,
        clock=FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)),
        latency=recorder,
    )
    await sink.start()
    for _ in range(3):
        sink.offer(AuthRequiredMessage())

    assert recorder.max_connection_queue_depth >= 1
