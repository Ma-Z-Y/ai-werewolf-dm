from __future__ import annotations

import math
from collections import deque
from typing import cast

from fastapi import APIRouter, Request

from werewolf_dm.application.rooms import RoomRegistry
from werewolf_dm.domain.model import StrictModel


class Metrics(StrictModel):
    active_rooms: int
    active_connections: int
    auth_failures: int
    slow_connection_closes: int
    command_latency_ms_p95: float
    broadcast_latency_ms_p95: float
    max_connection_queue_depth: int


class LatencyRecorder:
    def __init__(self, window: int) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        self.command_ms: deque[float] = deque(maxlen=window)
        self.broadcast_ms: deque[float] = deque(maxlen=window)
        self.max_connection_queue_depth = 0

    def observe_command_ms(self, value: float) -> None:
        self.command_ms.append(value)

    def observe_broadcast_ms(self, value: float) -> None:
        self.broadcast_ms.append(value)

    def observe_connection_queue_depth(self, value: int) -> None:
        self.max_connection_queue_depth = max(self.max_connection_queue_depth, value)

    @staticmethod
    def _p95(values: deque[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = math.ceil(len(ordered) * 0.95) - 1
        return ordered[index]

    def command_latency_ms_p95(self) -> float:
        return self._p95(self.command_ms)

    def broadcast_latency_ms_p95(self) -> float:
        return self._p95(self.broadcast_ms)


def metrics_router(
    registry: RoomRegistry | None,
    latency: LatencyRecorder,
) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics", response_model=Metrics)
    async def metrics(request: Request) -> Metrics:
        resolved_registry = registry
        if resolved_registry is None:
            resolved_registry = cast(RoomRegistry, request.app.state.room_registry)
        return Metrics(
            active_rooms=len(resolved_registry.rooms),
            active_connections=resolved_registry.active_connection_count(),
            auth_failures=resolved_registry.auth_failures,
            slow_connection_closes=resolved_registry.slow_connection_closes,
            command_latency_ms_p95=latency.command_latency_ms_p95(),
            broadcast_latency_ms_p95=latency.broadcast_latency_ms_p95(),
            max_connection_queue_depth=latency.max_connection_queue_depth,
        )

    return router
