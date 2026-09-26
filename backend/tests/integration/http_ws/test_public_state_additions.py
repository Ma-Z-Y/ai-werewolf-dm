from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tests.clocks import MutableClock
from tests.factories import (
    core_at_day_vote,
    core_at_pk_vote,
    submit_pk_votes,
    submit_votes,
)
from werewolf_dm.application.rooms import (
    HostControlUpdate,
    HostControlView,
    PublicViewUpdate,
    RoomActor,
    RoomSnapshot,
    SeatViewUpdate,
    SessionReadyUpdate,
)
from werewolf_dm.domain.contracts import AuthenticatedActor
from werewolf_dm.domain.model import GameState
from werewolf_dm.domain.state_machine import initial_state
from werewolf_dm.domain.visibility import (
    PublicView,
    PublicVoteProgress,
    SeatView,
    project_public_view,
    project_seat_view,
)
from werewolf_dm.interfaces.http_ws.app import create_app


class CapturingSubscriber:
    def __init__(
        self,
        *,
        actor_type: str,
        seat_id: int | None,
        session_id: UUID | None,
        channels: frozenset[str],
    ) -> None:
        self.subscription_id = uuid4()
        self.actor_type = actor_type
        self.seat_id = seat_id
        self.session_id = session_id
        self.channels = channels
        self.messages = []

    def offer(self, message) -> bool:
        self.messages.append(message)
        return True

    def request_close(self, code: int) -> None:
        del code


class CountingClock(MutableClock):
    def __init__(self, now: datetime) -> None:
        super().__init__(now)
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return super().__call__()


def test_public_view_paused_tracks_state_without_exposing_reason() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    state = initial_state(room_id=uuid4(), seed=1001)
    paused = GameState.revalidate(state.model_copy(update={"paused": True, "paused_at": clock()}))

    view = project_public_view(paused)

    assert view.paused is True
    assert "reason" not in PublicView.model_fields
    assert view.paused_at == paused.paused_at


def test_seat_view_paused_at_tracks_state() -> None:
    paused_at = datetime(2026, 9, 25, tzinfo=UTC)
    state = initial_state(room_id=uuid4(), seed=1001)
    paused = GameState.revalidate(state.model_copy(update={"paused": True, "paused_at": paused_at}))

    view = project_seat_view(
        paused,
        1,
        AuthenticatedActor(actor_type="seat", seat_id=1, room_id=paused.room_id),
    )

    assert view.paused is True
    assert view.paused_at == paused_at


def test_active_vote_summary_counts_unique_submitters_without_tallies() -> None:
    core = core_at_day_vote()
    submit_votes(core, {1: 2, 2: 3})
    submit_votes(core, {2: 4})

    summary = project_public_view(core.state).vote_summary

    assert summary is not None
    assert summary.submitted_count == 2
    assert summary.eligible_count == 6
    assert summary.tallies == ()
    assert set(summary.model_dump()) == {
        "round_id",
        "tallies",
        "abstention_count",
        "closed",
        "submitted_count",
        "eligible_count",
    }


def test_pk_vote_summary_uses_eligible_voters_without_targets() -> None:
    core = core_at_pk_vote()
    round_ = core.state.vote_round
    assert round_ is not None
    eligible = list(round_.eligible_voter_ids)
    candidate = round_.candidate_seat_ids[0]

    submit_pk_votes(core, {eligible[0]: candidate})
    summary = project_public_view(core.state).vote_summary

    assert summary is not None
    assert summary.submitted_count == 1
    assert summary.eligible_count == len(eligible)
    assert summary.tallies == ()
    assert "voter" not in summary.model_dump()
    assert "target" not in summary.model_dump()


def test_all_room_updates_carry_frozen_server_time() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    room = RoomActor(
        room_id=uuid4(),
        room_code="ROOM01",
        seed=1001,
        clock=clock,
        expires_at=clock() + timedelta(hours=1),
        last_activity_at=clock(),
    )
    subscriber = CapturingSubscriber(
        actor_type="host",
        seat_id=None,
        session_id=None,
        channels=frozenset({"public", "host.control"}),
    )

    async def scenario() -> None:
        await room.start()
        try:
            await room.attach_subscriber(subscriber)
            await room.publish_current(subscriber)
        finally:
            await room.stop()

    asyncio.run(scenario())

    assert [type(message) for message in subscriber.messages] == [
        SessionReadyUpdate,
        PublicViewUpdate,
        HostControlUpdate,
    ]
    assert {message.server_time for message in subscriber.messages} == {clock()}
    assert "server_time" not in RoomSnapshot.model_fields


def test_seat_update_carries_server_time() -> None:
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    room = RoomActor(
        room_id=uuid4(),
        room_code="ROOM01",
        seed=1001,
        clock=clock,
        expires_at=clock() + timedelta(hours=1),
        last_activity_at=clock(),
    )
    subscriber = CapturingSubscriber(
        actor_type="seat",
        seat_id=1,
        session_id=None,
        channels=frozenset({"public", "seat"}),
    )

    async def scenario() -> None:
        await room.start()
        try:
            await room.attach_subscriber(subscriber)
            await room.publish_current(subscriber)
        finally:
            await room.stop()

    asyncio.run(scenario())

    seat_updates = [
        message for message in subscriber.messages if isinstance(message, SeatViewUpdate)
    ]
    assert len(seat_updates) == 1
    assert seat_updates[0].server_time == clock()


def test_one_publish_reads_the_clock_once_for_all_subscribers() -> None:
    clock = CountingClock(datetime(2026, 9, 25, tzinfo=UTC))
    room = RoomActor(
        room_id=uuid4(),
        room_code="ROOM01",
        seed=1001,
        clock=clock,
        expires_at=clock() + timedelta(hours=1),
        last_activity_at=clock(),
    )
    host = CapturingSubscriber(
        actor_type="host",
        seat_id=None,
        session_id=None,
        channels=frozenset({"public", "host.control"}),
    )
    display = CapturingSubscriber(
        actor_type="display",
        seat_id=None,
        session_id=uuid4(),
        channels=frozenset({"public"}),
    )
    room.display_session_id = display.session_id

    async def scenario() -> None:
        await room.start()
        try:
            await room.attach_subscriber(host)
            await room.attach_subscriber(display)
            clock.calls = 0
            room._publish_updates()
            await asyncio.sleep(0)
        finally:
            await room.stop()

    asyncio.run(scenario())

    assert clock.calls == 1
    assert host.messages[-1].server_time == display.messages[-1].server_time


def test_one_attach_reads_the_clock_once() -> None:
    clock = CountingClock(datetime(2026, 9, 25, tzinfo=UTC))
    room = RoomActor(
        room_id=uuid4(),
        room_code="ROOM01",
        seed=1001,
        clock=clock,
        expires_at=clock() + timedelta(hours=1),
        last_activity_at=clock(),
    )
    subscriber = CapturingSubscriber(
        actor_type="host",
        seat_id=None,
        session_id=None,
        channels=frozenset({"public", "host.control"}),
    )

    async def scenario() -> None:
        await room.start()
        try:
            clock.calls = 0
            await room.attach_subscriber(subscriber)
        finally:
            await room.stop()

    asyncio.run(scenario())

    assert clock.calls == 1
    assert subscriber.messages[0].server_time == clock()


def test_projection_models_have_no_server_time() -> None:
    assert "server_time" not in PublicView.model_fields
    assert "server_time" not in SeatView.model_fields
    assert "server_time" not in HostControlView.model_fields
    assert "server_time" not in RoomSnapshot.model_fields
    assert "server_time" not in PublicVoteProgress.model_fields


def test_production_app_has_no_test_control_route() -> None:
    app = create_app()

    assert not any(path.startswith("/__test__") for path in app.openapi()["paths"])
    assert not any(
        route.path.startswith("/__test__") for route in app.routes if hasattr(route, "path")
    )
    with TestClient(app) as client:
        assert client.post("/__test__/rooms/ROOM01/advance").status_code == 404


def test_production_websocket_ignores_test_clock_message() -> None:
    app = create_app()
    with TestClient(app) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()
        registry = app.state.room_registry
        room = registry.get_by_code(created["room_code"])
        revision_before = room.core.state.revision

        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_json(
                {
                    "type": "auth",
                    "token": joined["seat_token"],
                    "last_seq": 0,
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_json({"type": "__test_advance_clock", "seconds": 10})
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["type"] == "pong"

        assert room.core.state.revision == revision_before
