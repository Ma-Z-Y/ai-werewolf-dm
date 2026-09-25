from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tests.factories import Scenario, core_at_wolf
from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomActor, RoomRegistry, SequenceTokenSource
from werewolf_dm.interfaces.http_ws.app import create_app

_START = datetime(2026, 9, 25, tzinfo=UTC)
_TOKEN_TTL = timedelta(hours=6)
_ROOM_CODE = "ROOM01"
_SECOND_ROOM_CODE = "ROOM02"
_HOST_TOKEN = "host-token-SENTINEL"
_SEAT_TOKEN = "seat-token-SENTINEL"
_SECOND_HOST_TOKEN = "second-host-token-SENTINEL"


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _register_actor(
    registry: RoomRegistry,
    *,
    scenario: Scenario,
    room_code: str,
    room_id: UUID | None = None,
) -> RoomActor:
    actor = RoomActor(
        room_id=scenario.core.state.room_id if room_id is None else room_id,
        room_code=room_code,
        seed=scenario.core.state.seed,
        clock=registry.clock,
        expires_at=registry.clock() + _TOKEN_TTL,
        last_activity_at=registry.clock(),
        core=scenario.core,
    )
    registry.rooms[room_code] = actor
    return actor


@contextmanager
def _export_client() -> Iterator[tuple[TestClient, RoomRegistry, Scenario]]:
    scenario = core_at_wolf()
    registry = RoomRegistry(
        clock=FrozenClock(_START),
        token_source=SequenceTokenSource(
            tokens=(_HOST_TOKEN, _SEAT_TOKEN, _SECOND_HOST_TOKEN),
            room_codes=(_ROOM_CODE, _SECOND_ROOM_CODE),
        ),
        seed_source=lambda: 101,
    )
    actor = _register_actor(
        registry,
        scenario=scenario,
        room_code=_ROOM_CODE,
    )
    registry.tokens.issue_host(actor.room_id, _TOKEN_TTL)
    registry.tokens.issue_seat(actor.room_id, 1, _TOKEN_TTL)

    with TestClient(create_app(registry), raise_server_exceptions=False) as client:
        yield client, registry, scenario


def test_player_replay_contains_only_public_and_own_private_facts() -> None:
    with _export_client() as (client, _, scenario):
        response = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )

    assert response.status_code == 200
    replay = response.json()
    assert set(replay) == {
        "room_id",
        "revision",
        "public_timeline",
        "private_facts",
        "events",
    }
    assert replay["room_id"] == str(scenario.core.state.room_id)
    assert replay["revision"] == scenario.core.state.revision

    seat_one_facts = tuple(
        fact for fact in scenario.core.state.private_facts if fact.recipient_seat_id == 1
    )
    assert [fact["fact_type"] for fact in replay["private_facts"]] == [
        fact.fact_type for fact in seat_one_facts
    ]
    assert [fact["fact_type"] for fact in replay["private_facts"]] == ["WITCH_POTIONS"]

    for event in replay["events"]:
        visibility = event["visibility"]
        assert visibility["scope"] == "public" or (
            visibility["scope"] == "seat" and visibility["seat_id"] == 1
        )

    role_events = [event for event in replay["events"] if event["event_type"] == "ROLE_ASSIGNED"]
    assert [event["fact_payload"]["role"] for event in role_events] == ["WITCH"]
    serialized = json.dumps(replay, ensure_ascii=False)
    assert "WOLF_TEAM" not in serialized
    assert "WOLF_DECISION" not in serialized
    assert "WEREWOLF" not in serialized
    assert "SEER" not in serialized


def test_player_replay_cannot_request_dm_trace_or_snapshots() -> None:
    with _export_client() as (client, _, _):
        response = client.request(
            "GET",
            f"/rooms/{_ROOM_CODE}/replay?include=dm_trace,snapshots",
            headers=_authorization(_SEAT_TOKEN),
            json={"include": ["dm_trace", "snapshots"]},
        )

    assert response.status_code == 200
    replay = response.json()
    assert replay.keys().isdisjoint({"dm_trace", "snapshots", "state", "raw_events"})


def test_host_audit_returns_complete_state_and_events() -> None:
    with _export_client() as (client, _, scenario):
        response = client.get(
            f"/rooms/{_ROOM_CODE}/audit",
            headers=_authorization(_HOST_TOKEN),
        )

    assert response.status_code == 200
    audit = response.json()
    assert set(audit) == {
        "room_id",
        "revision",
        "state",
        "raw_events",
        "dm_trace",
        "snapshots",
    }
    assert audit["room_id"] == str(scenario.core.state.room_id)
    assert audit["revision"] == scenario.core.state.revision
    assert audit["dm_trace"] == []
    assert audit["snapshots"] == []
    assert len(audit["raw_events"]) == len(scenario.core.events)

    roles = {player["seat_id"]: player["role"] for player in audit["state"]["players"]}
    assert roles == {
        1: "WITCH",
        2: "WEREWOLF",
        3: "VILLAGER",
        4: "SEER",
        5: "WEREWOLF",
        6: "VILLAGER",
    }
    hidden_role_events = [
        event for event in audit["raw_events"] if event["event_type"] == "ROLE_ASSIGNED"
    ]
    assert {event["visibility"]["seat_id"] for event in hidden_role_events} == {
        1,
        2,
        3,
        4,
        5,
        6,
    }


def test_seat_and_host_tokens_cannot_cross_export_scopes() -> None:
    with _export_client() as (client, _, _):
        seat_audit = client.get(
            f"/rooms/{_ROOM_CODE}/audit",
            headers=_authorization(_SEAT_TOKEN),
        )
        host_replay = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_HOST_TOKEN),
        )

    for response in (seat_audit, host_replay):
        assert response.status_code == 403
        assert response.json()["code"] == "ACTOR_NOT_AUTHORIZED"
        assert set(response.json()) == {"code", "message", "request_id"}


def test_export_requires_bearer_header_not_raw_dict_body() -> None:
    with _export_client() as (client, _, _):
        missing = client.get(f"/rooms/{_ROOM_CODE}/replay")
        raw_dict = client.request(
            "GET",
            f"/rooms/{_ROOM_CODE}/replay",
            json={"token": _SEAT_TOKEN},
        )

    for response in (missing, raw_dict):
        assert response.status_code == 401
        assert response.json()["code"] == "TOKEN_INVALID"
        assert set(response.json()) == {"code", "message", "request_id"}
        assert _SEAT_TOKEN not in response.text


def test_export_authorization_order_and_room_binding() -> None:
    with _export_client() as (client, registry, scenario):
        invalid_token = client.get(
            "/rooms/NO-SUCH/replay",
            headers=_authorization("invalid-token"),
        )
        missing_room = client.get(
            "/rooms/NO-SUCH/replay",
            headers=_authorization(_HOST_TOKEN),
        )

        second_room_id = uuid4()
        _register_actor(
            registry,
            scenario=scenario,
            room_code=_SECOND_ROOM_CODE,
            room_id=second_room_id,
        )
        registry.tokens.issue_host(second_room_id, _TOKEN_TTL)
        cross_room = client.get(
            f"/rooms/{_SECOND_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )

    assert invalid_token.status_code == 401
    assert invalid_token.json()["code"] == "TOKEN_INVALID"
    assert missing_room.status_code == 404
    assert missing_room.json()["code"] == "ROOM_NOT_FOUND"
    assert cross_room.status_code == 403
    assert cross_room.json()["code"] == "ACTOR_NOT_AUTHORIZED"


def test_exports_omit_tokens_digests_and_raw_exception_material() -> None:
    with _export_client() as (client, _, _):
        replay = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )
        audit = client.get(
            f"/rooms/{_ROOM_CODE}/audit",
            headers=_authorization(_HOST_TOKEN),
        )
        invalid_token = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers={"Authorization": "Bearer RAW-EXCEPTION-SENTINEL Traceback RuntimeError"},
        )

    assert replay.status_code == 200
    assert audit.status_code == 200
    exported = json.dumps(
        {"replay": replay.json(), "audit": audit.json()},
        ensure_ascii=False,
    )
    for forbidden in (_HOST_TOKEN, _SEAT_TOKEN, _digest(_HOST_TOKEN), _digest(_SEAT_TOKEN)):
        assert forbidden not in exported
    assert "RAW-EXCEPTION-SENTINEL" not in invalid_token.text
    assert "Traceback" not in invalid_token.text
    assert "RuntimeError" not in invalid_token.text
