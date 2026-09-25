from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from starlette.websockets import WebSocketDisconnect

from werewolf_dm.application.core import Clock
from werewolf_dm.domain.model import StrictModel


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
    ) -> None:
        self.websocket = websocket
        self.clock = clock
        self.subscription_id: UUID = uuid4()
        self.actor_type: Literal["seat", "host"] | None = None
        self.seat_id: int | None = None
        self.channels: frozenset[str] = frozenset()
        self.send_queue: asyncio.Queue[StrictModel] = asyncio.Queue(maxsize=queue_size)
        self.close_code: int | None = None
        self.last_client_activity_at = clock()
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

    def idle_expired(self, idle_timeout: float) -> bool:
        return self.clock() >= self.last_client_activity_at + timedelta(seconds=idle_timeout)

    def offer(self, message: StrictModel) -> bool:
        if self.close_code is not None:
            return False
        try:
            self.send_queue.put_nowait(message)
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
            message = await self.send_queue.get()
            try:
                await self.websocket.send_json(message.model_dump(mode="json"))
            except (RuntimeError, WebSocketDisconnect):
                return
            finally:
                self.send_queue.task_done()
