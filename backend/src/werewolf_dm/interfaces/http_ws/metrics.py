from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from typing import cast

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute
from pydantic import Field

from werewolf_dm.application.dm_metrics import (
    DMAggregateMetrics,
    DMMetrics,
    aggregate_dm_metrics,
    build_room_dm_metrics,
)
from werewolf_dm.application.rooms import RoomRegistry
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.interfaces.http_ws.audit import router as audit_router


class DMTemplateMetricsPayload(StrictModel):
    eligible: int = Field(ge=0, description="Eligible template domain slots.")
    template_admitted: int = Field(
        ge=0,
        description="Slots admitted to the wire outbox.",
    )
    render_failed: int = Field(
        ge=0,
        description="Slots that failed closed during rendering.",
    )
    slot_suppressed: int = Field(
        ge=0, description="Slots suppressed before admission; excludes transport failures."
    )
    template_admission_rate: float = Field(
        ge=0.0, le=1.0, description="template_admitted divided by completed admission terminals."
    )
    render_failure_rate: float = Field(
        ge=0.0, le=1.0, description="render_failed divided by completed admission terminals."
    )
    slot_suppressed_rate: float = Field(
        ge=0.0, le=1.0, description="slot_suppressed divided by completed admission terminals."
    )
    admission_ms_p95: float = Field(
        ge=0.0, description="Nearest-rank p95 of admission latency in milliseconds."
    )
    domain_transport_ratio: float = Field(
        ge=0.0, le=1.0, description="Mapped domain slots divided by completed domain slots."
    )
    max_domain_transport_lag: int = Field(
        ge=0, description="Maximum positive domain_seq minus transport_seq lag."
    )
    consistent: bool = Field(
        description=(
            "True when eligible equals completed admission terminals; "
            "transport failures are reported separately."
        )
    )

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
            consistent=metrics.consistent,
        )


class Metrics(StrictModel):
    active_rooms: int = Field(description="Current in-memory room count.")
    active_connections: int = Field(description="Current open connection count.")
    auth_failures: int = Field(description="Cumulative authentication failures.")
    slow_connection_closes: int = Field(description="Cumulative slow connection closes.")
    command_latency_ms_p95: float = Field(
        description="Command ACK admission-to-writer latency p95 in milliseconds."
    )
    broadcast_latency_ms_p95: float = Field(
        description="Broadcast admission-to-writer latency p95 in milliseconds."
    )
    max_connection_queue_depth: int = Field(description="Maximum observed connection queue depth.")
    dm_template: DMTemplateMetricsPayload | None = Field(
        default=None,
        description="Template-only DM metrics, omitted until template activity exists.",
    )


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
            actor.metrics_dm_trace or actor.metrics_dm_transport_trace or actor.domain_to_transport
        )
        room_metrics.append(
            build_room_dm_metrics(
                actor.metrics_dm_trace,
                actor.metrics_dm_transport_trace,
                actor.domain_to_transport,
            )
        )
    if not has_template_activity:
        return None
    return DMTemplateMetricsPayload.from_metrics(aggregate_dm_metrics(room_metrics))


def _host_audit_routes() -> tuple[APIRoute, ...]:
    return tuple(
        route
        for route in audit_router.routes
        if isinstance(route, APIRoute)
        and route.path.endswith("/{room_code}/audit")
        and route.methods is not None
        and "GET" in route.methods
    )


def _route_declares_include(route: APIRoute) -> bool:
    return any(
        parameter.name == "include" and parameter.field_info.description
        for parameter in route.dependant.query_params
    )


def _validate_audit_route_installation(
    routes: Iterable[APIRoute] | None = None,
) -> None:
    resolved = _host_audit_routes() if routes is None else tuple(routes)
    if len(resolved) != 1 or not _route_declares_include(resolved[0]):
        raise RuntimeError("DM_TRACE_AUDIT_ROUTE_INVALID")


_validate_audit_route_installation()
