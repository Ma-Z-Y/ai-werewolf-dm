from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from fastapi.testclient import TestClient

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.domain.contracts import (
    CommandEnvelope,
    Faction,
    HostCommand,
    HostPatchCommand,
    HostPauseCommand,
    HostRewindToSnapshotCommand,
    Phase,
    SetPhasePatch,
    SetPotionPatch,
    SnapshotReason,
)
from werewolf_dm.infrastructure.persistence import (
    PersistedSnapshot,
    SQLiteRoomStore,
)
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


def _connect(socket, token: str) -> dict[str, object]:
    assert socket.receive_json() == {"type": "auth.required"}
    socket.send_json({"type": "auth", "token": token, "last_seq": 0})
    ready = socket.receive_json()
    assert ready["type"] == "session.ready"
    return ready


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


def test_restart_restores_room_state_tokens_events_and_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove REC-006, REC-007, REC-008, REC-009, REC-013, and E2E-007.

    The same temporary SQLite path crosses two production app lifecycles.
    SnapshotReason.PHASE_START and SnapshotReason.GAME_END records are
    restored without duplication, while raw events and rejected/applied
    recovery audits remain intact.
    """

    database_path = tmp_path / "restart-recovery.sqlite3"
    monkeypatch.setenv("WEREWOLF_DM_DB_PATH", str(database_path))

    with TestClient(create_app(), raise_server_exceptions=False) as first:
        registry = first.app.state.room_registry
        assert isinstance(registry, RoomRegistry)
        store = registry.store
        assert isinstance(store, SQLiteRoomStore)

        created = first.post(
            "/rooms",
            json={"display_name": "Host"},
        ).json()
        joined = first.post(
            f"/rooms/{created['room_code']}/join",
            json={"display_name": "Alice"},
        ).json()
        pairing = first.post(
            f"/rooms/{created['room_code']}/display-pairings",
            headers=_authorization(created["host_token"]),
        ).json()
        display = first.post(
            f"/rooms/{created['room_code']}/display-sessions",
            json={"pairing_code": pairing["pairing_code"]},
        ).json()

        with first.websocket_connect("/ws") as host_socket:
            _connect(host_socket, created["host_token"])
            host_socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPatchCommand(patch=SetPhasePatch(phase=Phase.DAY_VOTE)),
                    expected_revision=0,
                )
            )
            _, rejected = _receive_ack(host_socket)
            assert rejected["accepted"] is False
            assert rejected["error_code"] == "ILLEGAL_PHASE"
            assert rejected["revision"] == 0

            host_socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostPauseCommand(reason="restart proof"),
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

            snapshots_response = first.get(
                f"/rooms/{created['room_code']}/audit?include=snapshots",
                headers=_authorization(created["host_token"]),
            )
            assert snapshots_response.status_code == 200
            pre_correction = max(
                (
                    item
                    for item in snapshots_response.json()["snapshots"]
                    if item["reason"] == SnapshotReason.PRE_CORRECTION.value
                ),
                key=lambda item: item["revision"],
            )
            host_socket.send_json(
                _command_frame(
                    room_id=UUID(created["room_id"]),
                    payload=HostRewindToSnapshotCommand(
                        snapshot_id=UUID(pre_correction["snapshot_id"])
                    ),
                    expected_revision=patch_ack["revision"],
                )
            )
            _, rewind_ack = _receive_ack(host_socket)
            assert rewind_ack["accepted"] is True

        probe = SQLiteRoomStore(database_path)
        state, events = probe.load_core(UUID(created["room_id"]))
        game_end_state = state.model_copy(update={"phase": Phase.GAME_END, "winner": Faction.GOOD})
        seeded = (
            PersistedSnapshot(
                snapshot_id=uuid5(
                    NAMESPACE_URL,
                    f"{created['room_id']}:{state.revision}:phase-start-proof",
                ),
                room_id=state.room_id,
                revision=state.revision,
                reason=SnapshotReason.PHASE_START,
                state=state,
                event_count=state.event_count,
                created_at=_START,
            ),
            PersistedSnapshot(
                snapshot_id=uuid5(
                    NAMESPACE_URL,
                    f"{created['room_id']}:{state.revision}:game-end-proof",
                ),
                room_id=state.room_id,
                revision=state.revision,
                reason=SnapshotReason.GAME_END,
                state=game_end_state,
                event_count=game_end_state.event_count,
                created_at=_START,
            ),
        )
        with probe.transaction():
            for snapshot in seeded:
                probe.save_snapshot(snapshot)

        expected_snapshots = probe.load_snapshots(UUID(created["room_id"]))
        expected_events = tuple(event.model_dump(mode="json") for event in events)
        expected_audit = probe.load_recovery_audit(UUID(created["room_id"]))
        expected_revision = state.revision
        expected_snapshot_ids = {snapshot.snapshot_id for snapshot in expected_snapshots}
        assert len(expected_snapshot_ids) == len(expected_snapshots)
        assert (
            Counter(snapshot.reason for snapshot in expected_snapshots)[SnapshotReason.PHASE_START]
            == 1
        )
        assert (
            Counter(snapshot.reason for snapshot in expected_snapshots)[SnapshotReason.GAME_END]
            == 1
        )
        assert any(record.status == "REJECTED" for record in expected_audit)
        assert any(
            record.status == "APPLIED" and record.patch_type == "HOST_REWIND_TO_SNAPSHOT"
            for record in expected_audit
        )
        probe.close()

    with TestClient(create_app(), raise_server_exceptions=False) as second:
        restored = second.app.state.room_registry
        assert isinstance(restored, RoomRegistry)
        restored_store = restored.store
        assert isinstance(restored_store, SQLiteRoomStore)
        probe = SQLiteRoomStore(database_path)
        restored_snapshots = probe.load_snapshots(UUID(created["room_id"]))
        assert len(restored_snapshots) == len(expected_snapshots)
        assert {snapshot.snapshot_id for snapshot in restored_snapshots} == expected_snapshot_ids
        assert len({snapshot.snapshot_id for snapshot in restored_snapshots}) == len(
            restored_snapshots
        )
        assert Counter(snapshot.reason for snapshot in restored_snapshots) == Counter(
            snapshot.reason for snapshot in expected_snapshots
        )

        with second.websocket_connect("/ws") as host_socket:
            ready = _connect(host_socket, created["host_token"])
            assert ready["snapshot"]["room_id"] == created["room_id"]
            assert ready["snapshot"]["revision"] == expected_revision
            assert ready["snapshot"]["host_control"]["paused"] is True

            with second.websocket_connect("/ws") as seat_socket:
                seat_ready = _connect(seat_socket, joined["seat_token"])
                assert seat_ready["snapshot"]["room_id"] == created["room_id"]
                assert seat_ready["snapshot"]["revision"] == expected_revision

                with second.websocket_connect("/ws") as display_socket:
                    display_ready = _connect(
                        display_socket,
                        display["display_token"],
                    )
                    assert display_ready["snapshot"]["room_id"] == created["room_id"]
                    assert display_ready["snapshot"]["revision"] == expected_revision

        audit_response = second.get(
            f"/rooms/{created['room_code']}/audit?include=recovery_audit,snapshots",
            headers=_authorization(created["host_token"]),
        )
        assert audit_response.status_code == 200
        audit = audit_response.json()
        assert tuple(event["event_id"] for event in audit["raw_events"]) == tuple(
            event["event_id"] for event in expected_events
        )
        assert {snapshot["snapshot_id"] for snapshot in audit["snapshots"]} == {
            str(snapshot_id) for snapshot_id in expected_snapshot_ids
        }
        assert len(audit["snapshots"]) == len(expected_snapshots)
        assert {record["record_id"] for record in audit["recovery_audit"]} == {
            str(record.record_id) for record in expected_audit
        }

        replay_response = second.get(
            f"/rooms/{created['room_code']}/replay?include=snapshots,recovery_audit",
            headers=_authorization(joined["seat_token"]),
        )
        assert replay_response.status_code == 200
        replay = replay_response.json()
        for forbidden in (
            "snapshots",
            "recovery_audit",
            "raw_events",
            "state",
            "dm_trace",
            "token",
        ):
            assert forbidden not in replay
        assert "HOST_CORRECTION_APPLIED" not in replay_response.text
        assert "HOST_COMPENSATION_APPLIED" not in replay_response.text
        seat_audit = second.get(
            f"/rooms/{created['room_code']}/audit?include=recovery_audit,snapshots",
            headers=_authorization(joined["seat_token"]),
        )
        assert seat_audit.status_code == 403
        assert "snapshot" not in seat_audit.text
        assert "recovery_audit" not in seat_audit.text
        probe.close()
