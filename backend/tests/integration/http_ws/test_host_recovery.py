from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.domain.contracts import (
    CommandEnvelope,
    HostCommand,
    HostPatchCommand,
    HostPauseCommand,
    HostRewindToSnapshotCommand,
    SetPotionPatch,
)
from werewolf_dm.infrastructure.persistence import SQLiteRoomStore
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink
from werewolf_dm.interfaces.http_ws.ws import message_loop

_START = datetime(2026, 9, 29, tzinfo=UTC)
_TTL = timedelta(hours=6)
_ROOM_CODE = "ROOM01"
_HOST_TOKEN = "host-token"
_SEAT_TOKEN = "seat-token"
_DISPLAY_TOKEN = "display-token"


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


def _open_registry(tmp_path: Path) -> tuple[SQLiteRoomStore, RoomRegistry]:
    store = SQLiteRoomStore(tmp_path / "host-recovery.sqlite3")
    store.migrate()
    registry = RoomRegistry(
        clock=FrozenClock(_START),
        token_source=SequenceTokenSource(
            tokens=(_HOST_TOKEN, _SEAT_TOKEN, _DISPLAY_TOKEN),
            room_codes=(_ROOM_CODE,),
        ),
        seed_source=lambda: 101,
        store=store,
    )
    return store, registry


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _app_with_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, dict[str, object]]:
    holder: dict[str, object] = {}

    def build_registry() -> RoomRegistry:
        store, registry = _open_registry(tmp_path)
        holder["store"] = store
        holder["registry"] = registry
        return registry

    monkeypatch.setattr(
        "werewolf_dm.interfaces.http_ws.app.build_production_registry",
        build_registry,
    )
    return TestClient(create_app(), raise_server_exceptions=False), holder


def _command_frame(
    *,
    room_id: UUID,
    payload: HostCommand,
    expected_revision: int,
) -> dict[str, object]:
    return {
        "type": "command",
        "command": CommandEnvelope(
            command_id=uuid4(),
            room_id=room_id,
            expected_revision=expected_revision,
            issued_at=_START,
            payload=payload,
        ).model_dump(mode="json"),
    }


def _connect(socket, token: str) -> None:
    assert socket.receive_json() == {"type": "auth.required"}
    socket.send_json({"type": "auth", "token": token, "last_seq": 0})
    assert socket.receive_json()["type"] == "session.ready"


def _receive_ack(socket) -> tuple[list[dict[str, object]], dict[str, object]]:
    messages: list[dict[str, object]] = []
    while True:
        message = socket.receive_json()
        messages.append(message)
        if message["type"] == "command.ack":
            return messages, message


@pytest.mark.asyncio
async def test_recovery_command_without_authenticated_actor_fails_closed(
    tmp_path: Path,
) -> None:
    store, registry = _open_registry(tmp_path)
    created = registry.create_room(_TTL)
    await registry.start_room(created.room_code)
    frame = _command_frame(
        room_id=created.room_id,
        payload=HostPatchCommand(
            patch=SetPotionPatch(
                antidote_available=False,
                poison_available=False,
            )
        ),
        expected_revision=0,
    )
    socket = ScriptedSocket([frame])
    sink = ConnectionSink(socket, clock=registry.clock)
    await sink.start()

    try:
        with pytest.raises(StopLoop):
            await message_loop(
                socket,
                sink,
                room=registry.get_by_code(created.room_code),
                actor=None,
                idle_timeout=60.0,
            )
        await sink.drain()
    finally:
        await sink.close(1000)
        await registry.remove_room(created.room_code)
        store.close()

    assert len(socket.sent) == 1
    assert socket.sent[0]["type"] == "error"
    assert socket.sent[0]["code"] == "ACTOR_NOT_AUTHORIZED"
    assert socket.sent[0]["message"] == "操作未授权"


def test_host_patch_requires_host_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, holder = _app_with_registry(tmp_path, monkeypatch)
    with client:
        registry = holder["registry"]
        assert isinstance(registry, RoomRegistry)
        created = client.post(
            "/rooms",
            json={"display_name": "Host"},
        ).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()

        with client.websocket_connect("/ws") as socket:
            _connect(socket, joined["seat_token"])
            socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPatchCommand(
                        patch=SetPotionPatch(
                            antidote_available=False,
                            poison_available=False,
                        )
                    ),
                    expected_revision=0,
                )
            )
            messages, ack = _receive_ack(socket)

        assert messages == [ack]
        assert ack["accepted"] is False
        assert ack["error_code"] == "ACTOR_NOT_AUTHORIZED"
        assert set(ack) == {
            "type",
            "command_id",
            "accepted",
            "revision",
            "error_code",
            "outbox_seq",
        }
        assert registry.get_by_code(_ROOM_CODE).core.state.paused is False


def test_host_patch_ack_contains_no_private_snapshot_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _app_with_registry(tmp_path, monkeypatch)
    with client:
        created = client.post(
            "/rooms",
            json={"display_name": "Host"},
        ).json()

        with client.websocket_connect("/ws") as socket:
            _connect(socket, created["host_token"])
            socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPauseCommand(reason="host recovery"),
                    expected_revision=0,
                )
            )
            _, pause_ack = _receive_ack(socket)
            assert pause_ack["accepted"] is True
            pause_revision = pause_ack["revision"]

            socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPatchCommand(
                        patch=SetPotionPatch(
                            antidote_available=False,
                            poison_available=False,
                        )
                    ),
                    expected_revision=pause_revision,
                )
            )
            messages, patch_ack = _receive_ack(socket)

        assert patch_ack["accepted"] is True
        assert set(patch_ack) == {
            "type",
            "command_id",
            "accepted",
            "revision",
            "error_code",
            "outbox_seq",
        }
        forbidden = {
            "diff",
            "snapshot",
            "snapshot_payload",
            "raw_events",
            "token",
            "internal_digest",
        }
        assert all(forbidden.isdisjoint(message) for message in messages)
        assert "HOST_CORRECTION_APPLIED" not in str(messages)


def test_host_rewind_after_pause_syncs_all_subscribers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, holder = _app_with_registry(tmp_path, monkeypatch)
    with client:
        store = holder["store"]
        assert isinstance(store, SQLiteRoomStore)
        created = client.post(
            "/rooms",
            json={"display_name": "Host"},
        ).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()
        pairing = client.post(
            f"/rooms/{created['room_code']}/display-pairings",
            headers=_authorization(created["host_token"]),
        ).json()
        display = client.post(
            f"/rooms/{created['room_code']}/display-sessions",
            json={"pairing_code": pairing["pairing_code"]},
        ).json()

        with client.websocket_connect("/ws") as host_socket:
            _connect(host_socket, created["host_token"])
            host_socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPauseCommand(reason="host recovery"),
                    expected_revision=0,
                )
            )
            _, pause_ack = _receive_ack(host_socket)
            assert pause_ack["accepted"] is True

            host_socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPatchCommand(
                        patch=SetPotionPatch(
                            antidote_available=False,
                            poison_available=True,
                        )
                    ),
                    expected_revision=pause_ack["revision"],
                )
            )
            _, patch_ack = _receive_ack(host_socket)
            assert patch_ack["accepted"] is True
            patch_revision = patch_ack["revision"]
            snapshots_response = client.get(
                f"/rooms/{created['room_code']}/audit?include=snapshots",
                headers=_authorization(created["host_token"]),
            )
            assert snapshots_response.status_code == 200
            snapshot = max(
                (
                    item
                    for item in snapshots_response.json()["snapshots"]
                    if item["reason"] == "PRE_CORRECTION"
                ),
                key=lambda item: item["revision"],
            )
            assert snapshot is not None

            with client.websocket_connect("/ws") as seat_socket:
                _connect(seat_socket, joined["seat_token"])
                with client.websocket_connect("/ws") as display_socket:
                    _connect(display_socket, display["display_token"])
                    host_socket.send_json(
                        _command_frame(
                            room_id=UUID(created["room_id"]),
                            payload=HostRewindToSnapshotCommand(
                                snapshot_id=UUID(snapshot["snapshot_id"])
                            ),
                            expected_revision=patch_revision,
                        )
                    )
                    rewind_messages, rewind_ack = _receive_ack(host_socket)
                    host_updates = [
                        message
                        for message in rewind_messages
                        if message["type"] == "host.control.updated"
                    ]
                    seat_updates = [
                        seat_socket.receive_json(),
                        seat_socket.receive_json(),
                    ]
                    display_update = display_socket.receive_json()

        assert rewind_ack["accepted"] is True
        assert "snapshot_id" not in rewind_ack
        assert host_updates[0]["host_control"]["revision"] == rewind_ack["revision"]
        assert all(
            message["type"] in {"public.view.updated", "seat.view.updated"}
            for message in seat_updates
        )
        assert display_update["type"] == "public.view.updated"
        assert display_update["public_view"]["revision"] == rewind_ack["revision"]
        assert all(
            message["outbox_seq"] == rewind_ack["outbox_seq"]
            for message in (*host_updates, *seat_updates, display_update)
        )


def test_recovery_audit_is_host_only_and_includes_persisted_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _app_with_registry(tmp_path, monkeypatch)
    with client:
        created = client.post(
            "/rooms",
            json={"display_name": "Host"},
        ).json()
        joined = client.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()

        with client.websocket_connect("/ws") as socket:
            _connect(socket, created["host_token"])
            socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPauseCommand(reason="host recovery"),
                    expected_revision=0,
                )
            )
            _, pause_ack = _receive_ack(socket)
            socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPatchCommand(
                        patch=SetPotionPatch(
                            antidote_available=False,
                            poison_available=False,
                        )
                    ),
                    expected_revision=pause_ack["revision"],
                )
            )
            _, patch_ack = _receive_ack(socket)
            assert patch_ack["accepted"] is True

        host_response = client.get(
            f"/rooms/{created['room_code']}/audit?include=recovery_audit,snapshots",
            headers=_authorization(created["host_token"]),
        )
        seat_response = client.get(
            f"/rooms/{created['room_code']}/audit?include=recovery_audit,snapshots",
            headers=_authorization(joined["seat_token"]),
        )

    assert host_response.status_code == 200
    audit = host_response.json()
    assert any(
        record["patch_type"] == "SET_POTION" and record["status"] == "APPLIED"
        for record in audit["recovery_audit"]
    )
    assert any(snapshot["reason"] == "PRE_CORRECTION" for snapshot in audit["snapshots"])
    assert seat_response.status_code == 403
    assert "recovery_audit" not in seat_response.text
    assert "snapshot" not in seat_response.text
