from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.interfaces.http_ws.app import create_app


@contextmanager
def make_client() -> Iterator[tuple[TestClient, RoomRegistry]]:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host", "seat-1"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )
    with TestClient(create_app(registry, token_ttl=timedelta(hours=7))) as client:
        yield client, registry


def test_create_and_join_room() -> None:
    with make_client() as (client, registry):
        created_response = client.post("/rooms", json={"display_name": "Host"})
        created = created_response.json()

        assert created_response.status_code == 201
        assert set(created) == {"room_id", "room_code", "host_token", "expires_at"}
        assert created["room_code"] == "ROOM01"
        assert created["host_token"] == "host"
        assert datetime.fromisoformat(created["expires_at"]) == datetime(
            2026,
            9,
            24,
            7,
            tzinfo=UTC,
        )

        actor = registry.get_by_code(created["room_code"])
        assert actor._task is not None
        assert not actor._task.done()
        core_state_before = (actor.core.state, actor.core.events)

        joined_response = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        )
        joined = joined_response.json()

        assert joined_response.status_code == 200
        assert set(joined) == {"room_id", "seat_id", "seat_token", "expires_at"}
        assert joined["room_id"] == created["room_id"]
        assert joined["seat_id"] == 1
        assert joined["seat_token"] == "seat-1"
        assert datetime.fromisoformat(joined["expires_at"]) == datetime(
            2026,
            9,
            24,
            4,
            tzinfo=UTC,
        )
        assert (actor.core.state, actor.core.events) == core_state_before
