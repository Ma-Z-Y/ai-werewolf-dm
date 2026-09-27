from __future__ import annotations

import math
from collections import deque
from typing import cast

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute

from werewolf_dm.application.dm_metrics import (
    DMAggregateMetrics,
    DMMetrics,
    aggregate_dm_metrics,
    build_room_dm_metrics,
    clip_dm_trace,
)
from werewolf_dm.application.rooms import RoomRegistry
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.domain.visibility import HostAuditExport, project_host_audit
from werewolf_dm.interfaces.http_ws.audit import router as audit_router
from werewolf_dm.interfaces.http_ws.authorization import authorize_bearer


class DMTemplateMetricsPayload(StrictModel):
    eligible: int
    template_admitted: int
    render_failed: int
    slot_suppressed: int
    template_admission_rate: float
    render_failure_rate: float
    slot_suppressed_rate: float
    admission_ms_p95: float
    domain_transport_ratio: float
    max_domain_transport_lag: int

    @classmethod
    def from_metrics(
        cls,
        metrics: DMMetrics | DMAggregateMetrics,
    ) -> DMTemplateMetricsPayload:
        return cls(
            eligible=metrics.eligible,
            template_admitted=metrics.template_admitted,
            render_failed=metrics.render_failed,
            slot_suppressed=metrics.slot_suppressed,
            template_admission_rate=metrics.template_admission_rate,
            render_failure_rate=metrics.render_failure_rate,
            slot_suppressed_rate=metrics.slot_suppressed_rate,
            admission_ms_p95=metrics.admission_ms_p95,
            domain_transport_ratio=metrics.domain_transport_ratio,
            max_domain_transport_lag=metrics.max_domain_transport_lag,
        )


class Metrics(StrictModel):
    active_rooms: int
    active_connections: int
    auth_failures: int
    slow_connection_closes: int
    command_latency_ms_p95: float
    broadcast_latency_ms_p95: float
    max_connection_queue_depth: int
    dm_template: DMTemplateMetricsPayload | None = None


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

    @router.get("/metrics", response_model=Metrics, response_model_exclude_none=True)
    async def metrics(request: Request) -> Metrics:
        resolved_registry = registry
        if resolved_registry is None:
            resolved_registry = cast(RoomRegistry, request.app.state.room_registry)
        template_metrics = _template_metrics_for_registry(resolved_registry)
        return Metrics(
            active_rooms=len(resolved_registry.rooms),
            active_connections=resolved_registry.active_connection_count(),
            auth_failures=resolved_registry.auth_failures,
            slow_connection_closes=resolved_registry.slow_connection_closes,
            command_latency_ms_p95=latency.command_latency_ms_p95(),
            broadcast_latency_ms_p95=latency.broadcast_latency_ms_p95(),
            max_connection_queue_depth=latency.max_connection_queue_depth,
            dm_template=template_metrics,
        )

    return router


def _template_metrics_for_registry(
    registry: RoomRegistry,
) -> DMTemplateMetricsPayload | None:
    room_metrics: list[DMMetrics] = []
    has_template_activity = False
    for actor in registry.rooms.values():
        has_template_activity = has_template_activity or bool(
            actor.dm_trace or actor.dm_transport_trace or actor.domain_to_transport
        )
        room_metrics.append(
            build_room_dm_metrics(
                actor.dm_trace,
                actor.dm_transport_trace,
                actor.domain_to_transport,
            )
        )
    if not has_template_activity:
        return None
    return DMTemplateMetricsPayload.from_metrics(aggregate_dm_metrics(room_metrics))


def _replace_host_audit_route() -> None:
    replacement = APIRouter(prefix="/rooms", tags=["exports"])

    @replacement.get("/{room_code}/audit", response_model=HostAuditExport)
    async def audit_with_dm_trace(
        room_code: str,
        request: Request,
        include: str = "",
    ) -> HostAuditExport:
        actor, authenticated, _ = authorize_bearer(request, room_code, "host")
        audit = project_host_audit(actor.core.state, actor.core.events, authenticated)
        requested = frozenset(value.strip() for value in include.split(",") if value.strip())
        if "dm_trace" not in requested:
            return audit
        return audit.model_copy(
            update={
                "dm_trace": clip_dm_trace(
                    actor.dm_trace,
                    actor.dm_transport_trace,
                )
            }
        )

    audit_router.routes[:] = [
        route
        for route in audit_router.routes
        if not (
            isinstance(route, APIRoute)
            and route.path.endswith("/{room_code}/audit")
            and route.methods is not None
            and "GET" in route.methods
        )
    ]
    audit_router.routes[0:0] = replacement.routes


_replace_host_audit_route()
