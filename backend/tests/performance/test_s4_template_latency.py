from __future__ import annotations

import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from time import perf_counter
from unittest.mock import patch

import pytest
from websockets.sync.client import ClientConnection

from tests.conftest import _core_with_wolf_win
from tests.factories import ROOM_ID
from tests.integration.http_ws.test_s4_outbox import EmptyRegistry, _actor
from tests.integration.http_ws.test_six_client_flow import (
    make_registry as make_six_client_registry,
)
from tests.integration.http_ws.test_six_client_flow import (
    open_authenticated_client,
    receive_json,
    running_server,
)
from werewolf_dm.application.dm_service import TemplateDMService
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink

SAMPLE_COUNT = 40
LAT004_ROUNDS = 20


class RecordingSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        del code


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = math.ceil(len(ordered) * 0.95) - 1
    return ordered[index]


async def _drain_sink(sink: ConnectionSink) -> None:
    await sink.drain()


@pytest.mark.asyncio
@pytest.mark.latency
async def test_lat_001_template_enqueue_p95_under_200ms() -> None:
    samples: list[float] = []

    for _ in range(SAMPLE_COUNT):
        actor = _actor(_core_with_wolf_win(poison_good=False))
        started = perf_counter()
        await actor.consume_announcements()
        samples.append((perf_counter() - started) * 1000.0)
        assert actor.published_messages

    assert _p95(samples) < 200


@pytest.mark.asyncio
@pytest.mark.latency
async def test_lat_004_six_client_dm_message_p95_under_300ms() -> None:
    registry = make_six_client_registry()
    with patch("werewolf_dm.application.rooms.uuid4", return_value=ROOM_ID):
        created = registry.create_room(timedelta(hours=1))
    joined = [registry.join_room(created.room_code, f"P{seat}") for seat in range(1, 7)]

    with running_server(registry) as (_, loop, port):
        cleanup = asyncio.run_coroutine_threadsafe(
            registry.start_room(created.room_code),
            loop,
        )
        cleanup.result(timeout=5)
        room = registry.get_by_code(created.room_code)
        with ExitStack() as stack:
            sockets = [
                open_authenticated_client(stack, port, joined_room.seat_token)[0]
                for joined_room in joined
            ]

            async def replace_and_admit() -> None:
                core = _core_with_wolf_win(poison_good=False)
                room.core = core
                room.clock = core.clock
                room._completed_domain_seqs.clear()
                room._announcement_slots.clear()
                room._announcement_candidates.clear()
                dm_domain_seq = max(
                    item.seq for item in core.state.outbox if item.kind == "dm.message"
                )
                await room.consume_slot(domain_seq=dm_domain_seq)

            def receive_dm_message(socket_: ClientConnection) -> float:
                while True:
                    message = receive_json(socket_)
                    if message.get("type") == "dm.message":
                        return (perf_counter() - started) * 1000.0

            samples: list[float] = []
            for _ in range(LAT004_ROUNDS):
                with ThreadPoolExecutor(max_workers=len(sockets)) as executor:
                    started = perf_counter()
                    pending = [executor.submit(receive_dm_message, socket_) for socket_ in sockets]
                    admitted = asyncio.run_coroutine_threadsafe(replace_and_admit(), loop)
                    admitted.result(timeout=5)
                    samples.extend(future.result(timeout=5) for future in pending)

    assert len(samples) == LAT004_ROUNDS * len(sockets)
    measured_p95 = _p95(samples)
    print(f"LAT-004 template six-client dm.message p95={measured_p95:.3f}ms samples={len(samples)}")
    assert measured_p95 < 300


@pytest.mark.asyncio
@pytest.mark.latency
async def test_lat_005_template_fail_closed_under_500ms() -> None:
    service = TemplateDMService(registry=EmptyRegistry())
    actor = _actor(
        _core_with_wolf_win(poison_good=False),
        dm_service=service,
    )

    started = perf_counter()
    await actor.consume_announcements()
    elapsed_ms = (perf_counter() - started) * 1000.0

    assert elapsed_ms < 500
    assert actor.published_messages == []
    assert actor.dm_trace
    assert any(trace.admission_status == "failed" for trace in actor.dm_trace)
    assert all(trace.admission_status != "admitted" for trace in actor.dm_trace)
