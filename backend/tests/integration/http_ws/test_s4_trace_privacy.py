from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.factories import core_at_wolf
from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.dm_contracts import DMTraceRecord
from werewolf_dm.application.rooms import RoomActor, RoomRegistry, SequenceTokenSource
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.audit import router as audit_router
from werewolf_dm.interfaces.http_ws.metrics import (
    DMTemplateMetricsPayload,
    Metrics,
    _validate_audit_route_installation,
)

_START = datetime(2026, 9, 27, tzinfo=UTC)
_ROOM_CODE = "ROOM01"
_HOST_TOKEN = "host-token-private"
_SEAT_TOKEN = "seat-token-private"
_HIDDEN_TEMPLATE_SENTINEL = "HIDDEN_TEMPLATE_SENTINEL"
_INCLUDE_DESCRIPTION = "Comma-separated audit sections; supports dm_trace."
_TRACE_KEYS = {
    "trace_id",
    "intent_id",
    "template_variant_id",
    "catalog_version",
    "source_event_ids",
    "channel",
    "audience_seat_ids",
    "admission_status",
    "suppress_reason",
    "elapsed_ms",
}


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _trace(
    *,
    intent_id: UUID,
    admission_status: str,
    suppress_reason: str | None,
    elapsed_ms: int,
) -> DMTraceRecord:
    return DMTraceRecord(
        trace_id=uuid4(),
        intent_id=intent_id,
        template_variant_id=_HIDDEN_TEMPLATE_SENTINEL,
        catalog_version="s4-template-v1",
        source_event_ids=(uuid4(),),
        channel="public",
        audience_seat_ids=(),
        admission_status=admission_status,
        suppress_reason=suppress_reason,
        elapsed_ms=elapsed_ms,
    )


@contextmanager
def _privacy_client() -> Iterator[tuple[TestClient, RoomActor]]:
    scenario = core_at_wolf()
    registry = RoomRegistry(
        clock=FrozenClock(_START),
        token_source=SequenceTokenSource(
            tokens=(_HOST_TOKEN, _SEAT_TOKEN),
            room_codes=(_ROOM_CODE,),
        ),
        seed_source=lambda: 101,
    )
    actor = RoomActor(
        room_id=scenario.core.state.room_id,
        room_code=_ROOM_CODE,
        seed=scenario.core.state.seed,
        clock=registry.clock,
        expires_at=registry.clock() + timedelta(hours=6),
        last_activity_at=registry.clock(),
        core=scenario.core,
    )
    registry.rooms[_ROOM_CODE] = actor
    registry.tokens.issue_host(actor.room_id, timedelta(hours=6))
    registry.tokens.issue_seat(actor.room_id, 1, timedelta(hours=6))
    intent_id = uuid4()
    actor.dm_trace.extend(
        (
            _trace(
                intent_id=intent_id,
                admission_status="admitted",
                suppress_reason=None,
                elapsed_ms=7,
            ),
            _trace(
                intent_id=uuid4(),
                admission_status="failed",
                suppress_reason=None,
                elapsed_ms=11,
            ),
        )
    )
    actor.dm_transport_trace.append(
        _trace(
            intent_id=intent_id,
            admission_status="suppressed",
            suppress_reason="transport_failed",
            elapsed_ms=13,
        )
    )
    actor.domain_to_transport[1] = 1

    with TestClient(create_app(registry), raise_server_exceptions=False) as client:
        yield client, actor


def test_trace_has_no_private_or_provider_fields() -> None:
    with _privacy_client() as (client, _):
        response = client.get(
            f"/rooms/{_ROOM_CODE}/audit?include=dm_trace",
            headers=_authorization(_HOST_TOKEN),
        )

    assert response.status_code == 200
    traces = response.json()["dm_trace"]
    assert len(traces) == 3
    assert all(set(trace) == _TRACE_KEYS for trace in traces)
    serialized = json.dumps(traces)
    for forbidden in (
        "raw_output",
        "raw_prompt",
        "provider",
        "model",
        "token",
        "session",
        "raw_facts",
        "speech",
        "state",
    ):
        assert forbidden not in serialized
    assert any(trace["suppress_reason"] == "transport_failed" for trace in traces)


def test_player_replay_omits_hidden_template_facts() -> None:
    with _privacy_client() as (client, _):
        response = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )

    assert response.status_code == 200
    replay = response.json()
    assert replay.keys().isdisjoint({"dm_trace", "dm_transport_trace"})
    assert _HIDDEN_TEMPLATE_SENTINEL not in response.text


def test_metrics_exposes_template_counters_rates_lag_and_ratio() -> None:
    with _privacy_client() as (client, _):
        response = client.get("/metrics")

    assert response.status_code == 200
    metrics = response.json()
    assert metrics["command_latency_ms_p95"] == 0.0
    assert metrics["broadcast_latency_ms_p95"] == 0.0
    template = metrics["dm_template"]
    assert template == {
        "eligible": 2,
        "template_admitted": 1,
        "render_failed": 1,
        "slot_suppressed": 0,
        "template_admission_rate": 0.5,
        "render_failure_rate": 0.5,
        "slot_suppressed_rate": 0.0,
        "admission_ms_p95": 11.0,
        "domain_transport_ratio": 0.5,
        "max_domain_transport_lag": 0,
    }
    assert (
        metrics["dm_template"]
        .keys()
        .isdisjoint({"provider", "model", "cold", "warm", "raw_output"})
    )


def test_audit_include_dm_trace_is_documented_in_openapi() -> None:
    with _privacy_client() as (client, _):
        openapi = client.get("/openapi.json").json()

    audit_routes = [
        route
        for route in audit_router.routes
        if isinstance(route, APIRoute)
        and route.path.endswith("/{room_code}/audit")
        and route.methods is not None
        and "GET" in route.methods
    ]
    assert len(audit_routes) == 1
    parameters = openapi["paths"]["/rooms/{room_code}/audit"]["get"]["parameters"]
    include_parameter = next(
        parameter for parameter in parameters if parameter["name"] == "include"
    )
    assert include_parameter["description"] == _INCLUDE_DESCRIPTION


def test_audit_route_installation_fails_if_dm_trace_query_missing() -> None:
    router = APIRouter(prefix="/rooms")

    @router.get("/{room_code}/audit")
    async def undocumented_audit(include: str = "") -> None:
        del include
        return None

    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    with pytest.raises(RuntimeError, match="DM_TRACE_AUDIT_ROUTE_INVALID"):
        _validate_audit_route_installation(routes)


def test_metrics_response_schema_matches_openapi() -> None:
    with _privacy_client() as (client, _):
        openapi = client.get("/openapi.json").json()

    schemas = openapi["components"]["schemas"]
    template_properties = schemas["DMTemplateMetricsPayload"]["properties"]
    metrics_properties = schemas["Metrics"]["properties"]

    assert set(template_properties) == set(DMTemplateMetricsPayload.model_fields)
    assert set(metrics_properties) == set(Metrics.model_fields)
    for name, field in DMTemplateMetricsPayload.model_fields.items():
        assert field.description
        assert template_properties[name]["description"] == field.description
    for name, field in Metrics.model_fields.items():
        assert field.description
        assert metrics_properties[name]["description"] == field.description
