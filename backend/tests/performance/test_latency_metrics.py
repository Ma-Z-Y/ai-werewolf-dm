from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.factories import make_envelope
from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.domain.contracts import JoinRoomCommand
from werewolf_dm.domain.state_machine import initial_state
from werewolf_dm.domain.visibility import project_public_view
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.metrics import LatencyRecorder
from werewolf_dm.interfaces.http_ws.models import CommandAckMessage, PublicViewMessage
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink


class RecordingSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.close_codes: list[int] = []
        self.sent: list[dict[str, object]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)


class AppAwareRecordingSocket(RecordingSocket):
    def __init__(self, recorder: LatencyRecorder) -> None:
        super().__init__()
        self.app = SimpleNamespace(
            state=SimpleNamespace(latency_recorder=recorder),
        )


def make_registry() -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 25, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host", "seat-1"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )


def make_message(outbox_seq: int = 1) -> PublicViewMessage:
    room_id = uuid4()
    return PublicViewMessage(
        outbox_seq=outbox_seq,
        public_view=project_public_view(initial_state(room_id, 101)),
    )


def test_metrics_endpoint_reports_strict_registry_and_latency_counters() -> None:
    registry = make_registry()
    registry.create_room(timedelta(hours=1))
    registry.connection_opened()
    registry.connection_opened()
    registry.auth_failures = 3
    registry.connection_closed(slow=True)
    latency = LatencyRecorder(window=100)
    for value in (1.0, 2.0, 3.0):
        latency.observe_command_ms(value)
        latency.observe_broadcast_ms(value)

    with TestClient(create_app(registry, latency=latency)) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.json() == {
        "active_rooms": 1,
        "active_connections": 1,
        "auth_failures": 3,
        "slow_connection_closes": 1,
        "command_latency_ms_p95": 3.0,
        "broadcast_latency_ms_p95": 3.0,
        "max_connection_queue_depth": 0,
    }


def test_latency_recorder_p95_uses_nearest_rank() -> None:
    recorder = LatencyRecorder(window=21)
    for value in range(1, 22):
        recorder.observe_broadcast_ms(float(value))

    assert recorder.broadcast_latency_ms_p95() == 20.0


async def test_connection_sink_uses_app_recorder_and_classifies_messages() -> None:
    recorder = LatencyRecorder(window=100)
    socket = AppAwareRecordingSocket(recorder)
    sink = ConnectionSink(socket, clock=FrozenClock(datetime.now(UTC)))
    await sink.start()
    broadcast = make_message()
    command_ack = CommandAckMessage(
        command_id=uuid4(),
        accepted=True,
        revision=1,
        outbox_seq=1,
    )

    assert sink.offer(broadcast)
    assert sink.offer(command_ack)
    await sink.drain()

    assert len(recorder.broadcast_ms) == 1
    assert len(recorder.command_ms) == 1


@pytest.mark.latency
async def test_template_enqueue_p95_under_200ms() -> None:
    recorder = LatencyRecorder(window=100)
    socket = RecordingSocket()
    sink = ConnectionSink(
        socket,
        clock=FrozenClock(datetime.now(UTC)),
        latency=recorder,
    )
    await sink.start()
    message = make_message()

    for _ in range(100):
        started = perf_counter()
        assert sink.queue_depth == 0
        assert sink.offer(message)
        recorder.observe_broadcast_ms((perf_counter() - started) * 1000.0)
        await sink.drain()

    assert recorder.broadcast_latency_ms_p95() < 200
    assert socket.accepted is True


@pytest.mark.latency
async def test_six_client_template_broadcast_p95_under_300ms() -> None:
    recorder = LatencyRecorder(window=100)
    sockets = [RecordingSocket() for _ in range(6)]
    sinks = [
        ConnectionSink(
            socket,
            clock=FrozenClock(datetime.now(UTC)),
            latency=recorder,
        )
        for socket in sockets
    ]
    for sink in sinks:
        await sink.start()

    message = make_message()
    started = perf_counter()
    for sink in sinks:
        assert sink.offer(message)
    for sink in sinks:
        await sink.drain()
    elapsed_ms = (perf_counter() - started) * 1000.0
    recorder.observe_broadcast_ms(elapsed_ms)

    assert recorder.broadcast_latency_ms_p95() < 300
    assert all(socket.sent == [message.model_dump(mode="json")] for socket in sockets)


@pytest.mark.latency
async def test_template_path_through_room_outbox_under_500ms() -> None:
    recorder = LatencyRecorder(window=100)
    clock = FrozenClock(datetime.now(UTC))
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(
            tokens=("host", "seat-1"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )
    created = registry.create_room(timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    await registry.start_room(created.room_code)
    room = registry.get_by_code(created.room_code)
    record = registry.tokens.resolve(joined.seat_token)
    actor = registry.actor_for(record)
    socket = RecordingSocket()
    sink = ConnectionSink(
        socket,
        clock=clock,
        latency=recorder,
    )
    await sink.start()
    sink.bind_actor(actor_type="seat", seat_id=record.seat_id)
    await room.attach_subscriber(sink)
    await sink.drain()
    envelope = make_envelope(
        actor,
        JoinRoomCommand(seat_id=1, display_name="Alice"),
        expected_revision=0,
        now=clock(),
    )

    try:
        ack = await room.submit_command(envelope, actor)
        await sink.drain()

        assert ack.accepted is True
        assert any(message["type"] == "public.view.updated" for message in socket.sent)
        assert any(message["type"] == "seat.view.updated" for message in socket.sent)
        measured_p95 = recorder.broadcast_latency_ms_p95()
        assert 0.0 < measured_p95 < 500
    finally:
        await sink.close(1000)
        await registry.remove_room(created.room_code)


@pytest.mark.latency
async def test_public_and_seat_messages_expose_bounded_queue_depth() -> None:
    recorder = LatencyRecorder(window=100)
    socket = RecordingSocket()
    sink = ConnectionSink(
        socket,
        clock=FrozenClock(datetime.now(UTC)),
        latency=recorder,
    )
    await sink.start()

    assert sink.offer(make_message())
    assert sink.queue_depth == 1
    assert sink.queue_depth <= sink.send_queue.maxsize

    await sink.drain()
    await asyncio.sleep(0)
    assert sink.queue_depth == 0


@pytest.mark.latency
@pytest.mark.skip(reason="LAT-002 belongs to S4 AI DM")
def test_complex_llm_broadcast_p99_under_two_seconds() -> None:
    raise AssertionError("S4 owns this test")


@pytest.mark.latency
@pytest.mark.skip(reason="LAT-003 belongs to S4 AI DM fallback")
def test_llm_timeout_falls_back_at_one_point_five_seconds() -> None:
    raise AssertionError("S4 owns this test")
