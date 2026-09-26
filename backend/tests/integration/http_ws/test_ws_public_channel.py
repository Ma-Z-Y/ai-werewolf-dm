from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.domain.visibility import project_public_view
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.models import PublicViewMessage

PUBLIC_VIEW_KEYS = {
    "schema_version",
    "room_id",
    "revision",
    "phase",
    "day",
    "living_seats",
    "public_timeline",
    "vote_summary",
    "deadline_at",
    "paused",
    "paused_at",
}


def make_registry(*, tokens: tuple[str, ...]) -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=tokens,
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )


def test_vis_006_public_channel_sends_projection_baseline_without_private_fields() -> None:
    registry = make_registry(tokens=("host", "seat-1"))
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()

        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_json(
                {
                    "type": "auth",
                    "token": joined["seat_token"],
                    "last_seq": 0,
                }
            )
            ready = socket.receive_json()
            socket.send_json({"type": "subscribe", "channel": "public"})
            message = socket.receive_json()

            parsed = PublicViewMessage.validate_json_payload(message)
            assert parsed.type == "public.view.updated"
            assert set(message) == {
                "type",
                "server_time",
                "outbox_seq",
                "public_view",
            }
            assert message["server_time"] == ready["server_time"]
            assert set(message["public_view"]) == PUBLIC_VIEW_KEYS
            assert "seat_id" not in message["public_view"]
            assert "private_facts" not in message["public_view"]
            assert "role" not in message["public_view"]
            assert "event_id" not in message["public_view"]
            assert message["outbox_seq"] == ready["snapshot"]["outbox_seq"]

            room = registry.get_by_code(created["room_code"])
            assert message["public_view"] == project_public_view(room.core.state).model_dump(
                mode="json"
            )


def test_public_subscription_preserves_default_seat_channel() -> None:
    registry = make_registry(tokens=("host", "seat-1"))
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()

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
            socket.send_json({"type": "subscribe", "channel": "public"})

            assert socket.receive_json()["type"] == "public.view.updated"
            seat_message = socket.receive_json()
            assert seat_message["type"] == "seat.view.updated"
            assert seat_message["seat_view"]["seat_id"] == joined["seat_id"]


def test_public_subscription_preserves_default_host_channel() -> None:
    registry = make_registry(tokens=("host",))
    with TestClient(create_app(registry)) as client:
        created = client.post("/rooms", json={"display_name": "Host"}).json()

        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "auth.required"
            socket.send_json(
                {
                    "type": "auth",
                    "token": created["host_token"],
                    "last_seq": 0,
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_json({"type": "subscribe", "channel": "public"})

            assert socket.receive_json()["type"] == "public.view.updated"
            host_message = socket.receive_json()
            assert host_message["type"] == "host.control.updated"
            assert host_message["host_control"]["revision"] == 0
