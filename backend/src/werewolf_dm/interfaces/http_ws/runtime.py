from __future__ import annotations

import asyncio
import contextlib
import logging
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

logger = logging.getLogger(__name__)


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


async def reap_periodically(
    registry: RoomRegistry,
    *,
    interval_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await registry.reap_expired()
        except Exception as exc:
            logger.error(
                "Room reaper failed exception_type=%s",
                type(exc).__name__,
            )
            await asyncio.sleep(1.0)


class WebSocketLike(Protocol):
    async def accept(self) -> None:
        raise NotImplementedError

    async def send_json(self, data: dict[str, object]) -> None:
        raise NotImplementedError

    async def close(self, code: int = 1000) -> None:
        raise NotImplementedError


class ConnectionSink:
    FULL_QUEUE_GRACE_SECONDS = 2.0

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
        self._closed_event = asyncio.Event()
        self._full_offer_timer: asyncio.TimerHandle | None = None

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
                self._schedule_full_queue_deadline()
            self._consecutive_full_offers += 1
            if self._consecutive_full_offers >= 3 or now >= self._first_full_offer_at + timedelta(
                seconds=self.FULL_QUEUE_GRACE_SECONDS
            ):
                self._schedule_close(1013)
            self._observe_queue_depth()
            return False
        self._clear_full_queue_tracking()
        self._observe_queue_depth()
        return True

    async def drain(self) -> None:
        if self.close_code is not None:
            await self.wait_closed()
            return
        join_task = asyncio.create_task(self.send_queue.join())
        closed_task = asyncio.create_task(self._closed_event.wait())
        done, pending = await asyncio.wait(
            {join_task, closed_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            with contextlib.suppress(asyncio.CancelledError, OSError, WebSocketDisconnect):
                await task

    async def close(self, code: int) -> None:
        self._schedule_close(code)
        await self.wait_closed()

    async def wait_closed(self) -> None:
        if self._close_task is not None:
            await self._close_task
        else:
            await self._closed_event.wait()

    def request_close(self, code: int) -> None:
        self._schedule_close(code)

    def _schedule_close(self, code: int) -> None:
        if self.close_code is not None:
            return
        self.close_code = code
        self._close_task = asyncio.create_task(self._finish_close(code))

    async def _finish_close(self, code: int) -> None:
        try:
            self._clear_full_queue_tracking()
            writer = self._writer_task
            if writer is not None and writer is not asyncio.current_task():
                writer.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError,
                    OSError,
                    WebSocketDisconnect,
                    RuntimeError,
                ):
                    await writer
            while True:
                try:
                    self.send_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    self.send_queue.task_done()
            with contextlib.suppress(
                asyncio.CancelledError,
                OSError,
                WebSocketDisconnect,
                RuntimeError,
            ):
                await self.websocket.close(code=code)
        finally:
            self._closed_event.set()

    async def _writer_loop(self) -> None:
        while True:
            message, enqueued_at = await self.send_queue.get()
            try:
                await self.websocket.send_json(message.model_dump(mode="json"))
            except (RuntimeError, OSError, WebSocketDisconnect):
                self._schedule_close(1011)
                return
            else:
                self._observe_delivery(message, enqueued_at)
            finally:
                self.send_queue.task_done()
                if not self.send_queue.full():
                    self._clear_full_queue_tracking()

    def _schedule_full_queue_deadline(self) -> None:
        if self._full_offer_timer is not None:
            return
        self._full_offer_timer = asyncio.get_running_loop().call_later(
            self.FULL_QUEUE_GRACE_SECONDS,
            self._check_full_queue_deadline,
        )

    def _check_full_queue_deadline(self) -> None:
        self._full_offer_timer = None
        if self._first_full_offer_at is not None and self.send_queue.full():
            self._schedule_close(1013)

    def _clear_full_queue_tracking(self) -> None:
        self._consecutive_full_offers = 0
        self._first_full_offer_at = None
        if self._full_offer_timer is not None:
            self._full_offer_timer.cancel()
            self._full_offer_timer = None

    def _observe_queue_depth(self) -> None:
        if self._latency is not None:
            self._latency.observe_connection_queue_depth(self.send_queue.qsize())

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
