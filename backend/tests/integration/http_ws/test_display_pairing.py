import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.clocks import MutableClock
from werewolf_dm.application.rooms import (
    AttachSubscriberEvent,
    RoomActor,
    RoomRegistry,
    RoomUpdate,
    SequenceTokenSource,
)
from werewolf_dm.domain.contracts import (
    CommandEnvelope,
    CommandPayload,
    HostPauseCommand,
    SetReadyCommand,
)
from werewolf_dm.interfaces.http_ws.app import create_app


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 9, 25, tzinfo=UTC))


@pytest.fixture
def registry(clock: MutableClock) -> RoomRegistry:
    source = SequenceTokenSource(
        tokens=tuple(f"token-{index}" for index in range(30)),
        room_codes=("ROOM01", "ROOM02"),
    )
    return RoomRegistry(
        clock=clock,
        token_source=source,
        seed_source=lambda: 1001,
    )


@pytest.fixture
def app(registry: RoomRegistry):
    return create_app(registry=registry)


@pytest.fixture
def app_client(app) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def created(app_client: TestClient) -> dict[str, str]:
    response = app_client.post("/rooms", json={"display_name": "host"})
    assert response.status_code == 201
    return response.json()


def _host_headers(created: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {created['host_token']}"}


def _create_pairing(
    client: TestClient,
    created: dict[str, str],
) -> dict[str, str]:
    response = client.post(
        f"/rooms/{created['room_code']}/display-pairings",
        headers=_host_headers(created),
    )
    assert response.status_code == 201
    return response.json()


def _exchange(
    client: TestClient,
    created: dict[str, str],
    pairing_code: str,
):
    return client.post(
        f"/rooms/{created['room_code']}/display-sessions",
        json={"pairing_code": pairing_code},
    )


def _wrong_code(code: str) -> str:
    return "000000" if code != "000000" else "000001"


def test_host_creates_and_stage_exchanges_pairing_code(
    app_client: TestClient,
    created: dict[str, str],
    clock: MutableClock,
) -> None:
    pairing = _create_pairing(app_client, created)
    assert set(pairing) == {"pairing_code", "expires_at"}
    assert len(pairing["pairing_code"]) == 6
    assert pairing["pairing_code"].isdigit()
    assert datetime.fromisoformat(pairing["expires_at"]) == clock() + timedelta(minutes=5)

    exchanged = _exchange(app_client, created, pairing["pairing_code"])
    assert exchanged.status_code == 201
    assert set(exchanged.json()) == {"room_id", "display_token", "expires_at"}
    assert exchanged.json()["room_id"] == created["room_id"]
    assert exchanged.json()["display_token"]
    assert datetime.fromisoformat(exchanged.json()["expires_at"]) == clock() + timedelta(hours=1)

    reused = _exchange(app_client, created, pairing["pairing_code"])
    assert reused.status_code == 401
    assert reused.json()["code"] == "TOKEN_INVALID"


def test_pairing_code_is_room_bound(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    second = app_client.post(
        "/rooms",
        json={"display_name": "host-2"},
    ).json()
    pairing = _create_pairing(app_client, created)

    response = _exchange(app_client, second, pairing["pairing_code"])

    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_INVALID"


def test_pairing_code_expires_after_five_minutes(
    app_client: TestClient,
    created: dict[str, str],
    clock: MutableClock,
) -> None:
    pairing = _create_pairing(app_client, created)

    clock.advance(minutes=5)
    response = _exchange(app_client, created, pairing["pairing_code"])

    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_INVALID"


def test_pairing_code_invalidates_after_five_failures(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    pairing = _create_pairing(app_client, created)

    responses = [
        _exchange(app_client, created, _wrong_code(pairing["pairing_code"])) for _ in range(5)
    ]

    assert [response.status_code for response in responses] == [401] * 5
    assert {response.json()["code"] for response in responses} == {"TOKEN_INVALID"}
    response = _exchange(app_client, created, pairing["pairing_code"])
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_INVALID"


def test_pairing_attempts_are_rate_limited_by_room_and_source(
    app_client: TestClient,
    app,
    created: dict[str, str],
) -> None:
    pairing = _create_pairing(app_client, created)
    for _ in range(10):
        response = _exchange(app_client, created, _wrong_code(pairing["pairing_code"]))
        assert response.status_code == 401

    limited = _exchange(app_client, created, _wrong_code(pairing["pairing_code"]))
    assert limited.status_code == 429
    assert limited.json()["code"] == "RATE_LIMITED"

    second = app_client.post(
        "/rooms",
        json={"display_name": "host-2"},
    ).json()
    second_pairing = _create_pairing(app_client, second)
    second_room_exchange = _exchange(
        app_client,
        second,
        second_pairing["pairing_code"],
    )
    assert second_room_exchange.status_code == 201

    new_pairing = _create_pairing(app_client, created)
    with TestClient(
        app,
        raise_server_exceptions=False,
        client=("203.0.113.7", 50000),
    ) as other_source:
        other_source_exchange = _exchange(
            other_source,
            created,
            new_pairing["pairing_code"],
        )
    assert other_source_exchange.status_code == 201


def test_only_host_token_can_create_pairing(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    joined = app_client.post(
        f"/rooms/{created['room_code']}/join",
        json={"display_name": "Alice"},
    ).json()

    response = app_client.post(
        f"/rooms/{created['room_code']}/display-pairings",
        headers={"Authorization": f"Bearer {joined['seat_token']}"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_NOT_AUTHORIZED"


def test_display_expiry_is_capped_by_room_expiry(
    registry: RoomRegistry,
    created: dict[str, str],
    clock: MutableClock,
) -> None:
    room = registry.get_by_code(created["room_code"])
    room.expires_at = clock() + timedelta(minutes=10)
    pairing_code = registry.create_display_pairing(created["room_code"])[0]

    _, _, expires_at = registry.exchange_display_pairing(
        created["room_code"],
        pairing_code,
        "127.0.0.1",
    )

    assert expires_at == room.expires_at


def test_display_expiry_is_capped_when_clock_advances_during_exchange() -> None:
    clock = _JumpClock(datetime(2026, 9, 25, tzinfo=UTC))
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(
            tokens=("host", "display"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 1001,
    )
    created = registry.create_room(timedelta(hours=6))
    room = registry.get_by_code(created.room_code)
    pairing_code = registry.create_display_pairing(created.room_code)[0]
    room.expires_at = clock() + timedelta(seconds=10)
    clock.jump_before_call(4, timedelta(seconds=1))

    _, _, expires_at = registry.exchange_display_pairing(
        created.room_code,
        pairing_code,
        "127.0.0.1",
    )

    assert expires_at == room.expires_at


def test_host_revokes_display_session_idempotently(
    app_client: TestClient,
    created: dict[str, str],
    registry: RoomRegistry,
) -> None:
    joined = app_client.post(
        f"/rooms/{created['room_code']}/join",
        json={"display_name": "Alice"},
    ).json()
    pairing = _create_pairing(app_client, created)
    display_token = _exchange(
        app_client,
        created,
        pairing["pairing_code"],
    ).json()["display_token"]
    url = f"/rooms/{created['room_code']}/display-sessions/current"

    seat_revoke = app_client.delete(
        url,
        headers={"Authorization": f"Bearer {joined['seat_token']}"},
    )
    display_revoke = app_client.delete(
        url,
        headers={"Authorization": f"Bearer {display_token}"},
    )
    assert seat_revoke.status_code == 403
    assert seat_revoke.json()["code"] == "ACTOR_NOT_AUTHORIZED"
    assert display_revoke.status_code == 403
    assert display_revoke.json()["code"] == "ACTOR_NOT_AUTHORIZED"

    first = app_client.delete(url, headers=_host_headers(created))
    second = app_client.delete(url, headers=_host_headers(created))

    assert first.status_code == 204
    assert second.status_code == 204
    room = registry.get_by_code(created["room_code"])
    assert room.display_session_id is None
    assert room.display_token_digest is None
    assert registry.tokens.display_record(room.room_id) is None
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        registry.tokens.resolve(display_token)


def test_new_display_token_replaces_previous_session(
    registry: RoomRegistry,
    created: dict[str, str],
) -> None:
    first_code = registry.create_display_pairing(created["room_code"])[0]
    _, first_token, _ = registry.exchange_display_pairing(
        created["room_code"],
        first_code,
        "127.0.0.1",
    )
    first_record = registry.tokens.resolve(first_token)

    second_code = registry.create_display_pairing(created["room_code"])[0]
    _, second_token, _ = registry.exchange_display_pairing(
        created["room_code"],
        second_code,
        "127.0.0.1",
    )
    second_record = registry.tokens.resolve(second_token)

    assert second_record.session_id != first_record.session_id
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        registry.tokens.resolve(first_token)
    room = registry.get_by_code(created["room_code"])
    assert room.display_session_id == second_record.session_id
    assert room.display_token_digest == registry.tokens.digest(second_token)


@pytest.mark.asyncio
async def test_new_display_session_closes_only_stale_subscriber(
    registry: RoomRegistry,
) -> None:
    created = registry.create_room(timedelta(hours=6))
    room = registry.get_by_code(created.room_code)
    await room.start()
    stale = _FakeSubscriber(session_id=uuid4())
    current = _FakeSubscriber(session_id=uuid4())
    try:
        room.display_session_id = stale.session_id
        await room.attach_subscriber(stale)
        room.display_session_id = current.session_id
        await room.attach_subscriber(current)

        room.sync_display_session()
        async with asyncio.timeout(1.0):
            while not stale.close_codes:
                await asyncio.sleep(0)

        assert stale.close_codes == [4001]
        assert current.close_codes == []
    finally:
        await room.stop()


@pytest.mark.asyncio
async def test_remove_room_cleans_pairings_attempts_and_display_token(
    registry: RoomRegistry,
) -> None:
    created = registry.create_room(timedelta(hours=6))
    room = registry.get_by_code(created.room_code)
    pairing_code = registry.create_display_pairing(created.room_code)[0]
    _, display_token, _ = registry.exchange_display_pairing(
        created.room_code,
        pairing_code,
        "127.0.0.1",
    )

    await registry.remove_room(created.room_code)

    assert room.room_id not in registry._display_pairings
    assert all(key[0] != room.room_id for key in registry._pairing_attempts)
    assert registry.tokens.display_record(room.room_id) is None
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        registry.tokens.resolve(display_token)


def _exchange_display(
    client: TestClient,
    created: dict[str, str],
) -> dict[str, str]:
    pairing = _create_pairing(client, created)
    response = _exchange(client, created, pairing["pairing_code"])
    assert response.status_code == 201
    return response.json()


def _authenticate_display(
    socket,
    token: str,
) -> dict:
    assert socket.receive_json() == {"type": "auth.required"}
    socket.send_json({"type": "auth", "token": token, "last_seq": 0})
    return socket.receive_json()


def _command_frame(
    room_id: str,
    payload: CommandPayload,
    *,
    expected_revision: int,
) -> dict:
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=UUID(room_id),
        expected_revision=expected_revision,
        issued_at=datetime(2026, 9, 25, tzinfo=UTC),
        payload=payload,
    )
    return {
        "type": "command",
        "command": envelope.model_dump(mode="json"),
    }


def test_display_can_read_public_but_cannot_send_command(
    app_client: TestClient,
    created: dict[str, str],
    registry: RoomRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display = _exchange_display(app_client, created)
    room = registry.get_by_code(created["room_code"])
    revision_before = room.core.state.revision

    def fail_submit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("display command entered GameCore.submit")

    monkeypatch.setattr(room.core, "submit", fail_submit)

    with app_client.websocket_connect("/ws") as socket:
        ready = _authenticate_display(socket, display["display_token"])
        assert ready["snapshot"]["seat_view"] is None
        assert ready["snapshot"]["host_control"] is None
        assert ready["snapshot"]["public_view"] is not None

        socket.send_json(
            _command_frame(
                ready["snapshot"]["room_id"],
                SetReadyCommand(ready=True),
                expected_revision=ready["snapshot"]["revision"],
            )
        )
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "ACTOR_NOT_AUTHORIZED"

    assert room.core.state.revision == revision_before


@pytest.mark.parametrize(
    "subscription",
    [
        {"type": "subscribe", "channel": "host.control"},
        {"type": "subscribe", "channel": "seat", "seat_id": 1},
        {"type": "subscribe", "channel": []},
    ],
)
def test_display_subscription_outside_public_is_forbidden(
    app_client: TestClient,
    created: dict[str, str],
    subscription: dict,
) -> None:
    display = _exchange_display(app_client, created)

    with app_client.websocket_connect("/ws") as socket:
        _authenticate_display(socket, display["display_token"])
        socket.send_json(subscription)
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "CHANNEL_FORBIDDEN"


def test_seat_cannot_subscribe_host_control(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    seat = app_client.post(
        f"/rooms/{created['room_code']}/join",
        json={"display_name": "Alice"},
    ).json()

    with app_client.websocket_connect("/ws") as socket:
        _authenticate_display(socket, seat["seat_token"])
        socket.send_json({"type": "subscribe", "channel": "host.control"})
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "CHANNEL_FORBIDDEN"


def test_host_subscribe_to_host_control_is_idempotent_noop(
    app_client: TestClient,
    created: dict[str, str],
    registry: RoomRegistry,
) -> None:
    with app_client.websocket_connect("/ws") as socket:
        _authenticate_display(socket, created["host_token"])
        room = registry.get_by_code(created["room_code"])
        assert len(room.subscribers) == 1

        socket.send_json({"type": "subscribe", "channel": "host.control"})
        socket.send_json({"type": "ping"})

        assert socket.receive_json() == {"type": "pong"}
        assert len(room.subscribers) == 1


def test_same_display_token_reconnect_replaces_old_socket_with_4003(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    display = _exchange_display(app_client, created)

    with app_client.websocket_connect("/ws") as first:
        _authenticate_display(first, display["display_token"])
        with app_client.websocket_connect("/ws") as second:
            _authenticate_display(second, display["display_token"])
            with pytest.raises(WebSocketDisconnect) as exc_info:
                first.receive_json()
            assert exc_info.value.code == 4003


def test_rotated_display_token_closes_old_socket_with_4001(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    first_display = _exchange_display(app_client, created)

    with app_client.websocket_connect("/ws") as socket:
        _authenticate_display(socket, first_display["display_token"])
        _exchange_display(app_client, created)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            socket.receive_json()
        assert exc_info.value.code == 4001


def test_rotation_does_not_close_the_new_display_session(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    first_display = _exchange_display(app_client, created)
    with app_client.websocket_connect("/ws") as first:
        _authenticate_display(first, first_display["display_token"])
        second_display = _exchange_display(app_client, created)
        with app_client.websocket_connect("/ws") as second:
            _authenticate_display(second, second_display["display_token"])
            with pytest.raises(WebSocketDisconnect):
                first.receive_json()
            second.send_json({"type": "ping"})
            assert second.receive_json() == {"type": "pong"}


def test_rotation_then_pause_only_updates_new_display_with_shared_server_time(
    app_client: TestClient,
    created: dict[str, str],
    registry: RoomRegistry,
) -> None:
    first_display = _exchange_display(app_client, created)
    with (
        app_client.websocket_connect("/ws") as first,
        app_client.websocket_connect("/ws") as host,
    ):
        _authenticate_display(first, first_display["display_token"])
        _authenticate_display(host, created["host_token"])
        second_display = _exchange_display(app_client, created)

        with app_client.websocket_connect("/ws") as second:
            _authenticate_display(second, second_display["display_token"])
            with pytest.raises(WebSocketDisconnect) as exc_info:
                while True:
                    frame = first.receive_json()
                    assert frame["type"] != "public.view.updated"
            assert exc_info.value.code == 4001

            room = registry.get_by_code(created["room_code"])
            host.send_json(
                _command_frame(
                    created["room_id"],
                    HostPauseCommand(reason="SECRET_REASON_SENTINEL"),
                    expected_revision=room.core.state.revision,
                )
            )

            display_update = second.receive_json()
            host_public = host.receive_json()
            assert display_update["type"] == "public.view.updated"
            assert host_public["type"] == "public.view.updated"
            assert display_update["server_time"] == host_public["server_time"]
            assert display_update["public_view"]["paused"] is True
            assert display_update["public_view"]["public_timeline"][-1]["statement"] == "游戏已暂停"
            assert host.receive_json()["type"] == "host.control.updated"
            assert host.receive_json()["type"] == "command.ack"


def test_revoking_display_closes_existing_socket_with_4001(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    display = _exchange_display(app_client, created)

    with app_client.websocket_connect("/ws") as socket:
        _authenticate_display(socket, display["display_token"])
        response = app_client.delete(
            f"/rooms/{created['room_code']}/display-sessions/current",
            headers=_host_headers(created),
        )
        assert response.status_code == 204
        with pytest.raises(WebSocketDisconnect) as exc_info:
            socket.receive_json()
        assert exc_info.value.code == 4001


def test_display_token_cannot_use_replay_or_audit(
    app_client: TestClient,
    created: dict[str, str],
) -> None:
    display = _exchange_display(app_client, created)
    headers = {"Authorization": f"Bearer {display['display_token']}"}

    assert (
        app_client.get(
            f"/rooms/{created['room_code']}/replay",
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        app_client.get(
            f"/rooms/{created['room_code']}/audit",
            headers=headers,
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "stale_field",
    ["display_token_digest", "display_session_id"],
)
def test_message_loop_rechecks_display_identity_before_handling_input(
    app_client: TestClient,
    created: dict[str, str],
    registry: RoomRegistry,
    stale_field: str,
) -> None:
    display = _exchange_display(app_client, created)
    room = registry.get_by_code(created["room_code"])

    with app_client.websocket_connect("/ws") as socket:
        _authenticate_display(socket, display["display_token"])
        if stale_field == "display_token_digest":
            room.display_token_digest = "stale-digest"
        else:
            room.display_session_id = uuid4()
        socket.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            socket.receive_json()
        assert exc_info.value.code == 4001


@pytest.mark.parametrize(
    "stale_session_id",
    [None, uuid4()],
    ids=["revoked", "rotated"],
)
def test_stale_display_is_closed_before_public_broadcast(
    app_client: TestClient,
    created: dict[str, str],
    registry: RoomRegistry,
    stale_session_id: UUID | None,
) -> None:
    display = _exchange_display(app_client, created)
    room = registry.get_by_code(created["room_code"])

    with (
        app_client.websocket_connect("/ws") as display_socket,
        app_client.websocket_connect("/ws") as host_socket,
    ):
        _authenticate_display(display_socket, display["display_token"])
        _authenticate_display(host_socket, created["host_token"])
        room.display_session_id = stale_session_id

        host_socket.send_json(
            _command_frame(
                created["room_id"],
                HostPauseCommand(reason="broadcast-after-display-invalidation"),
                expected_revision=room.core.state.revision,
            )
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            while True:
                frame = display_socket.receive_json()
                assert frame["type"] != "public.view.updated"
        assert exc_info.value.code == 4001

        assert host_socket.receive_json()["type"] == "public.view.updated"
        assert host_socket.receive_json()["type"] == "host.control.updated"
        assert host_socket.receive_json()["type"] == "command.ack"


def test_stale_display_attach_is_rejected_before_session_ready() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    room = RoomActor(
        room_id=uuid4(),
        room_code="ROOM01",
        seed=1001,
        clock=clock,
        expires_at=clock() + timedelta(hours=1),
        last_activity_at=clock(),
    )
    stale = _StaleDisplaySubscriber(uuid4())
    room.display_session_id = uuid4()

    async def scenario() -> None:
        future = asyncio.get_running_loop().create_future()
        await room.events.put(
            AttachSubscriberEvent(
                subscriber=stale,
                result=future,
                initial=True,
            )
        )
        room.display_session_id = uuid4()
        await room.start()
        try:
            await future
            assert stale.subscription_id not in room.subscribers
        finally:
            await room.stop()

    asyncio.run(scenario())

    assert stale.messages == []
    assert stale.close_codes == [4001]


class _StaleDisplaySubscriber:
    def __init__(self, session_id: UUID) -> None:
        self.subscription_id = uuid4()
        self.actor_type = "display"
        self.seat_id = None
        self.session_id = session_id
        self.channels = frozenset({"public"})
        self.messages: list[RoomUpdate] = []
        self.close_codes: list[int] = []

    def offer(self, message: RoomUpdate) -> bool:
        self.messages.append(message)
        return True

    def request_close(self, code: int) -> None:
        self.close_codes.append(code)


class _FakeSubscriber:
    def __init__(self, *, session_id: UUID) -> None:
        self.subscription_id = uuid4()
        self.actor_type = "display"
        self.seat_id = None
        self.session_id = session_id
        self.channels = frozenset({"public"})
        self.close_codes: list[int] = []

    def offer(self, message: RoomUpdate) -> bool:
        del message
        return True

    def request_close(self, code: int) -> None:
        self.close_codes.append(code)


class _JumpClock(MutableClock):
    def __init__(self, now: datetime) -> None:
        super().__init__(now)
        self._calls = 0
        self._jump_before: int | None = None
        self._jump = timedelta()

    def jump_before_call(self, call_number: int, jump: timedelta) -> None:
        self._calls = 0
        self._jump_before = call_number
        self._jump = jump

    def __call__(self) -> datetime:
        self._calls += 1
        if self._calls == self._jump_before:
            self.advance(seconds=self._jump.total_seconds())
            self._jump_before = None
        return super().__call__()
