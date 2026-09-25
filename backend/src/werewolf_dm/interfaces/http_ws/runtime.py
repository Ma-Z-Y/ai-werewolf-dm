from __future__ import annotations

import asyncio
import contextlib
import secrets
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Literal, Protocol
from uuid import UUID, uuid4

from starlette.websockets import WebSocketDisconnect

from werewolf_dm.application.core import Clock
from werewolf_dm.application.rooms import RoomRegistry, SecretsTokenSource
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.interfaces.http_ws.metrics import LatencyRecorder


class RealClock:
    def __call__(self) -> datetime:
        return datetime.now(UTC)

    def set(self, now: datetime) -> None:
        del now


def build_production_registry() -> RoomRegistry:
    return RoomRegistry(
        clock=RealClock(),
        token_source=SecretsTokenSource(),
        seed_source=lambda: secrets.randbits(63),
    )


class WebSocketLike(Protocol):
    async def accept(self) -> None:
        raise NotImplementedError

    async def send_json(self, data: dict[str, object]) -> None:
        raise NotImplementedError

    async def close(self, code: int = 1000) -> None:
        raise NotImplementedError


class ConnectionSink:
    def __init__(
        self,
        websocket: WebSocketLike,
        *,
        clock: Clock,
        queue_size: int = 64,
        latency: LatencyRecorder | None = None,
    ) -> None:
        self.websocket = websocket
        self.clock = clock
        self.subscription_id: UUID = uuid4()
        self.actor_type: Literal["seat", "host"] | None = None
        self.seat_id: int | None = None
        self.channels: frozenset[str] = frozenset()
        self.send_queue: asyncio.Queue[tuple[StrictModel, float]] = asyncio.Queue(
            maxsize=queue_size
        )
        self.close_code: int | None = None
        self.last_client_activity_at = clock()
        self._latency = latency or self._latency_from_websocket(websocket)
        self._consecutive_full_offers = 0
        self._first_full_offer_at: datetime | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.websocket.accept()
        if self._writer_task is None:
            self._writer_task = asyncio.create_task(self._writer_loop())

    def bind_actor(
        self,
        *,
        actor_type: Literal["seat", "host"],
        seat_id: int | None,
    ) -> None:
        if actor_type == "seat" and seat_id is None:
            raise ValueError("ACTOR_NOT_AUTHORIZED")
        if actor_type == "host" and seat_id is not None:
            raise ValueError("ACTOR_NOT_AUTHORIZED")
        self.actor_type = actor_type
        self.seat_id = seat_id
        self.channels = (
            frozenset({"public", "seat"})
            if actor_type == "seat"
            else frozenset({"public", "host.control"})
        )

    def set_channels(self, channels: frozenset[str]) -> None:
        self.channels = channels

    def record_client_activity(self) -> None:
        self.last_client_activity_at = self.clock()

    @property
    def queue_depth(self) -> int:
        return self.send_queue.qsize()

    def idle_expired(self, idle_timeout: float) -> bool:
        return self.clock() >= self.last_client_activity_at + timedelta(seconds=idle_timeout)

    def offer(self, message: StrictModel) -> bool:
        if self.close_code is not None:
            return False
        try:
            self.send_queue.put_nowait((message, perf_counter()))
        except asyncio.QueueFull:
            now = self.clock()
            if self._first_full_offer_at is None:
                self._first_full_offer_at = now
            self._consecutive_full_offers += 1
            if self._consecutive_full_offers >= 3 or now >= self._first_full_offer_at + timedelta(
                seconds=2
            ):
                self._schedule_close(1013)
            return False
        self._consecutive_full_offers = 0
        self._first_full_offer_at = None
        return True

    async def drain(self) -> None:
        await self.send_queue.join()

    async def close(self, code: int) -> None:
        self._schedule_close(code)
        await self.wait_closed()

    async def wait_closed(self) -> None:
        if self._close_task is not None:
            await self._close_task

    def _schedule_close(self, code: int) -> None:
        if self.close_code is not None:
            return
        self.close_code = code
        self._close_task = asyncio.create_task(self._finish_close(code))

    async def _finish_close(self, code: int) -> None:
        writer = self._writer_task
        if writer is not None and writer is not asyncio.current_task():
            writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer
        with contextlib.suppress(RuntimeError):
            await self.websocket.close(code=code)

    async def _writer_loop(self) -> None:
        while True:
            message, enqueued_at = await self.send_queue.get()
            try:
                await self.websocket.send_json(message.model_dump(mode="json"))
            except (RuntimeError, WebSocketDisconnect):
                return
            else:
                self._observe_delivery(message, enqueued_at)
            finally:
                self.send_queue.task_done()

    def _observe_delivery(self, message: StrictModel, enqueued_at: float) -> None:
        if self._latency is None:
            return
        elapsed_ms = (perf_counter() - enqueued_at) * 1000.0
        if getattr(message, "type", None) == "command.ack":
            self._latency.observe_command_ms(elapsed_ms)
        elif getattr(message, "type", None) in {
            "public.view.updated",
            "seat.view.updated",
            "host.control.updated",
        }:
            self._latency.observe_broadcast_ms(elapsed_ms)

    @staticmethod
    def _latency_from_websocket(websocket: WebSocketLike) -> LatencyRecorder | None:
        app = getattr(websocket, "app", None)
        state = getattr(app, "state", None)
        recorder = getattr(state, "latency_recorder", None)
        return recorder if isinstance(recorder, LatencyRecorder) else None
