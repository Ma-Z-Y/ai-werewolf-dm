from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.factories import core_at_wolf
from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.domain.contracts import CommandEnvelope, CommandErrorCode, SpeakCommand
from werewolf_dm.domain.visibility import ProjectionAccessError
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.errors import ErrorCode, ErrorResponse, sanitize_error
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink
from werewolf_dm.interfaces.http_ws.ws import message_loop


class StopLoop(Exception):
    pass


class ScriptedSocket:
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.sent: list[dict[str, object]] = []
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict[str, object]) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)

    async def receive_json(self) -> object:
        if self.messages:
            return self.messages.pop(0)
        raise StopLoop


def make_registry(tokens: tuple[str, ...] = ("host", "seat-1")) -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=tokens,
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )


def illegal_command_message(room_id: UUID, command_id: UUID) -> dict[str, object]:
    envelope = CommandEnvelope(
        command_id=command_id,
        room_id=room_id,
        expected_revision=0,
        issued_at=datetime(2026, 9, 24, tzinfo=UTC),
        payload=SpeakCommand(text="not my turn"),
    )
    return {
        "type": "command",
        "command": envelope.model_dump(mode="json"),
    }


def assert_no_forbidden_response_text(
    payloads: list[dict[str, object]],
    *,
    forbidden_values: tuple[str, ...],
) -> None:
    serialized = json.dumps(payloads, ensure_ascii=False)
    for forbidden in ("Traceback", "event_id", "RuntimeError", "ValueError", "private_facts"):
        assert forbidden not in serialized
    for forbidden in forbidden_values:
        assert forbidden not in serialized


def real_hidden_fact_markers() -> tuple[str, ...]:
    state = core_at_wolf().core.state
    roles = {player.role.value for player in state.players if player.role is not None}
    fact_types = {fact.fact_type for fact in state.private_facts}
    return tuple(sorted(roles | fact_types))


def test_error_code_is_strict_superset_and_unknown_errors_are_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert {code.value for code in CommandErrorCode} <= {code.value for code in ErrorCode}

    request_id = uuid4()
    sentinel = "SENTINEL_TOKEN_FRAGMENT"
    with caplog.at_level(logging.ERROR):
        response = sanitize_error(
            RuntimeError(f"{sentinel} Traceback WEREWOLF"),
            request_id=request_id,
        )

    assert response == ErrorResponse(
        code=ErrorCode.INTERNAL_ERROR,
        message="服务器内部错误",
        request_id=request_id,
    )
    serialized = response.model_dump_json()
    assert sentinel not in serialized
    assert "Traceback" not in serialized
    assert "WEREWOLF" not in serialized
    assert sentinel in caplog.text


def test_known_exception_codes_map_to_safe_messages_without_reflection() -> None:
    invalid_token = sanitize_error(ValueError("TOKEN_INVALID"))
    wrong_projection = sanitize_error(ProjectionAccessError("SEAT_VIEW_FORBIDDEN"))
    unknown_value_error = sanitize_error(ValueError("SENTINEL_INTERNAL_DETAIL"))

    assert invalid_token.code is ErrorCode.TOKEN_INVALID
    assert invalid_token.message == "令牌无效"
    assert "bad-token-fragment" not in invalid_token.model_dump_json()
    assert wrong_projection.code is ErrorCode.ACTOR_NOT_AUTHORIZED
    assert unknown_value_error.code is ErrorCode.INTERNAL_ERROR
    assert "SENTINEL_INTERNAL_DETAIL" not in unknown_value_error.model_dump_json()


def test_http_validation_unknown_room_and_internal_errors_are_safe() -> None:
    registry = make_registry()
    app = create_app(registry)
    sentinel = "SENTINEL_INTERNAL_ROOM_OBJECT"

    @app.get("/_test/internal-error")
    async def internal_error() -> None:
        raise RuntimeError(f"{sentinel} Traceback WEREWOLF private_facts")

    with TestClient(app, raise_server_exceptions=False) as client:
        invalid = client.post("/rooms", json={"unexpected": "field"})
        missing_room = client.post(
            "/rooms/NO-SUCH/join",
            json={"display_name": "Alice"},
        )
        internal = client.get("/_test/internal-error")

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "BAD_REQUEST"
    assert missing_room.status_code == 404
    assert missing_room.json()["code"] == "ROOM_NOT_FOUND"
    assert internal.status_code == 500
    assert internal.json()["code"] == "INTERNAL_ERROR"
    assert set(invalid.json()) == {"code", "message", "request_id"}
    assert set(missing_room.json()) == {"code", "message", "request_id"}
    assert set(internal.json()) == {"code", "message", "request_id"}
    assert_no_forbidden_response_text(
        [invalid.json(), missing_room.json(), internal.json()],
        forbidden_values=(sentinel, "NO-SUCH"),
    )


def test_projection_and_disconnect_handlers_are_registered() -> None:
    app = create_app(make_registry())

    @app.get("/_test/projection-error")
    async def projection_error() -> None:
        raise ProjectionAccessError("SEAT_VIEW_FORBIDDEN")

    @app.get("/_test/projection-integrity-error")
    async def projection_integrity_error() -> None:
        raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")

    assert ProjectionAccessError in app.exception_handlers
    assert WebSocketDisconnect in app.exception_handlers

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/projection-error")
        integrity_response = client.get("/_test/projection-integrity-error")

    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_NOT_AUTHORIZED"
    assert set(response.json()) == {"code", "message", "request_id"}
    assert_no_forbidden_response_text(
        [response.json()],
        forbidden_values=("SEAT_VIEW_FORBIDDEN",),
    )
    assert integrity_response.status_code == 500
    assert integrity_response.json()["code"] == "INTERNAL_ERROR"
    assert set(integrity_response.json()) == {"code", "message", "request_id"}
    assert_no_forbidden_response_text(
        [integrity_response.json()],
        forbidden_values=("PROJECTION_EVENT_MISMATCH",),
    )


def test_invalid_token_closes_without_echoing_the_token() -> None:
    registry = make_registry()
    token_fragment = "INVALID-TOKEN-FRAGMENT"
    with TestClient(create_app(registry)) as client:
        client.post("/rooms", json={"display_name": "Host"})
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws") as websocket,
        ):
            auth_required = websocket.receive_json()
            websocket.send_json(
                {
                    "type": "auth",
                    "token": token_fragment,
                    "last_seq": 0,
                }
            )
            websocket.receive_json()

    assert exc.value.code == 4001
    assert_no_forbidden_response_text([auth_required], forbidden_values=(token_fragment,))


def test_illegal_command_and_cross_seat_error_are_sanitized() -> None:
    registry = make_registry()
    hidden_facts = real_hidden_fact_markers()
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()
        room_id = UUID(created["room_id"])

        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json()["type"] == "auth.required"
            websocket.send_json(
                {
                    "type": "auth",
                    "token": joined["seat_token"],
                    "last_seq": 0,
                }
            )
            ready = websocket.receive_json()
            assert ready["type"] == "session.ready"

            websocket.send_json(illegal_command_message(room_id, uuid4()))
            illegal = websocket.receive_json()
            websocket.send_json({"type": "subscribe", "channel": "seat", "seat_id": 5})
            forbidden = websocket.receive_json()

    assert set(illegal) == {
        "type",
        "command_id",
        "accepted",
        "revision",
        "error_code",
        "outbox_seq",
    }
    assert illegal["error_code"] == "ILLEGAL_PHASE"
    assert set(forbidden) == {"type", "code", "message", "request_id"}
    assert forbidden["code"] == "CHANNEL_FORBIDDEN"
    UUID(forbidden["request_id"])
    assert_no_forbidden_response_text(
        [illegal, forbidden],
        forbidden_values=(
            *hidden_facts,
            str(room_id),
            joined["seat_token"],
            joined["seat_token"][:4],
            hashlib.sha256(joined["seat_token"].encode()).hexdigest(),
        ),
    )


def test_vis_007_real_hidden_facts_and_token_material_never_appear_in_errors() -> None:
    hidden_facts = real_hidden_fact_markers()
    raw_token = "SENTINEL-RAW-TOKEN-8b84d8f3e97048c7"
    raw_token_fragment = raw_token[:16]
    token_digest = hashlib.sha256(raw_token.encode()).hexdigest()
    token_digest_fragment = token_digest[:16]
    exception_text = " ".join(
        [
            *hidden_facts,
            raw_token,
            raw_token_fragment,
            token_digest,
            token_digest_fragment,
        ]
    )

    response = sanitize_error(RuntimeError(exception_text))

    assert response.code is ErrorCode.INTERNAL_ERROR
    assert_no_forbidden_response_text(
        [response.model_dump(mode="json")],
        forbidden_values=(
            *hidden_facts,
            raw_token,
            raw_token_fragment,
            token_digest,
            token_digest_fragment,
        ),
    )


@pytest.mark.asyncio
async def test_command_flood_returns_rate_limited_and_closes_after_strikes() -> None:
    registry = make_registry()
    created = registry.create_room(timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    await registry.start_room(created.room_code)
    flood = [illegal_command_message(created.room_id, uuid4()) for _ in range(23)]
    socket = ScriptedSocket(list(flood))
    sink = ConnectionSink(socket, clock=registry.clock)
    record = registry.tokens.resolve(joined.seat_token)
    actor = registry.actor_for(record)
    sink.bind_actor(actor_type="seat", seat_id=record.seat_id)
    await sink.start()

    try:
        await message_loop(
            socket,
            sink,
            room=registry.get_by_code(created.room_code),
            record=record,
            actor=actor,
            idle_timeout=60.0,
        )
        await sink.drain()
    finally:
        await sink.close(1000)
        await registry.remove_room(created.room_code)

    acks = [message for message in socket.sent if message["type"] == "command.ack"]
    rate_limited = [message for message in socket.sent if message["type"] == "error"]
    assert len(acks) == 20
    assert len(rate_limited) == 3
    assert {message["code"] for message in rate_limited} == {"RATE_LIMITED"}
    assert all(
        set(message) == {"type", "code", "message", "request_id"} for message in rate_limited
    )
    assert socket.close_codes == [1008]
    assert_no_forbidden_response_text(
        [*acks, *rate_limited],
        forbidden_values=(joined.seat_token, str(created.room_id)),
    )


@pytest.mark.asyncio
async def test_ping_uses_a_separate_small_bucket() -> None:
    registry = make_registry()
    created = registry.create_room(timedelta(hours=1))
    joined = registry.join_room(created.room_code, "Alice")
    await registry.start_room(created.room_code)
    socket = ScriptedSocket(
        [
            {"type": "ping"},
            {"type": "ping"},
            {"type": "ping"},
            {"type": "ping"},
            {"type": "ping"},
            {"type": "ping"},
        ]
    )
    sink = ConnectionSink(socket, clock=registry.clock)
    record = registry.tokens.resolve(joined.seat_token)
    actor = registry.actor_for(record)
    sink.bind_actor(actor_type="seat", seat_id=record.seat_id)
    await sink.start()

    try:
        await message_loop(
            socket,
            sink,
            room=registry.get_by_code(created.room_code),
            record=record,
            actor=actor,
            idle_timeout=60.0,
        )
        await sink.drain()
    finally:
        await sink.close(1000)
        await registry.remove_room(created.room_code)

    assert [message["type"] for message in socket.sent] == [
        "pong",
        "pong",
        "pong",
        "error",
        "error",
        "error",
    ]
    assert [message.get("code") for message in socket.sent if message["type"] == "error"] == [
        "RATE_LIMITED",
        "RATE_LIMITED",
        "RATE_LIMITED",
    ]
    assert socket.close_codes == [1008]
