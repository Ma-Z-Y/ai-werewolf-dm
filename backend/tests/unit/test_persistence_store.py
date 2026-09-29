import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from werewolf_dm.application.dm_contracts import DMTraceRecord
from werewolf_dm.domain.contracts import (
    CommandDedupeKey,
    CommandResult,
    DomainEvent,
    PublicVisibility,
    ReadyChangedPayload,
    RoomJoinedPayload,
    SnapshotReason,
    build_event,
)
from werewolf_dm.domain.enums import EventType, Phase
from werewolf_dm.domain.model import GameState
from werewolf_dm.domain.replay import event_log_digest
from werewolf_dm.infrastructure.persistence import (
    PersistedRecoveryAudit,
    PersistedRoom,
    PersistedRoomRuntime,
    PersistedSnapshot,
    PersistedToken,
    PersistenceConflictError,
    PersistenceCorruptionError,
    PersistenceNotFoundError,
    SQLiteRoomStore,
)
from werewolf_dm.interfaces.http_ws import runtime

ROOM_ID = UUID("00000000-0000-0000-0000-000000000001")
COMMAND_ID = UUID("10000000-0000-0000-0000-000000000001")
SECOND_COMMAND_ID = UUID("10000000-0000-0000-0000-000000000002")
TRACE_ID = UUID("20000000-0000-0000-0000-000000000001")
TRANSPORT_TRACE_ID = UUID("20000000-0000-0000-0000-000000000002")
INTENT_ID = UUID("30000000-0000-0000-0000-000000000001")
PUBLISHED_MESSAGE_ID = UUID("40000000-0000-0000-0000-000000000001")
SECOND_PUBLISHED_MESSAGE_ID = UUID("40000000-0000-0000-0000-000000000002")
SNAPSHOT_ID = UUID("50000000-0000-0000-0000-000000000001")
AUDIT_ID = UUID("60000000-0000-0000-0000-000000000001")
CREATED_AT = datetime(2026, 9, 28, tzinfo=UTC)
EXPIRES_AT = datetime(2026, 9, 29, tzinfo=UTC)
TOKEN_DIGEST = "a" * 64


def sample_room_id() -> UUID:
    return ROOM_ID


def sample_events() -> tuple[DomainEvent, ...]:
    return (
        build_event(
            cause_id=COMMAND_ID,
            event_ordinal=0,
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.ROOM_JOINED,
            visibility=PublicVisibility(),
            payload=RoomJoinedPayload(seat_id=1, display_name="Alpha"),
            created_at=CREATED_AT,
        ),
    )


def state_for_events(events: tuple[DomainEvent, ...], *, revision: int) -> GameState:
    return GameState(
        room_id=ROOM_ID,
        seed=7,
        revision=revision,
        event_count=len(events),
        event_log_digest=event_log_digest(events),
        phase=Phase.LOBBY,
        day=0,
    )


def sample_state() -> GameState:
    return state_for_events(sample_events(), revision=1)


def sample_appended_event() -> DomainEvent:
    return build_event(
        cause_id=SECOND_COMMAND_ID,
        event_ordinal=1,
        room_id=ROOM_ID,
        revision=2,
        event_type=EventType.READY_CHANGED,
        visibility=PublicVisibility(),
        payload=ReadyChangedPayload(seat_id=1, ready=True),
        created_at=CREATED_AT,
    )


def sample_persisted_room() -> PersistedRoom:
    return PersistedRoom(
        room_code="AGT001",
        room_id=ROOM_ID,
        seed=7,
        rulepack_version="1.1.0",
        expires_at=EXPIRES_AT,
        last_activity_at=CREATED_AT,
    )


def sample_persisted_token() -> PersistedToken:
    return PersistedToken(
        token_digest=TOKEN_DIGEST,
        room_id=ROOM_ID,
        actor_type="host",
        seat_id=None,
        session_id=None,
        issued_at=CREATED_AT,
        expires_at=EXPIRES_AT,
        revoked=False,
    )


def sample_command_key() -> CommandDedupeKey:
    return CommandDedupeKey(
        command_id=COMMAND_ID,
        actor_type="host",
        actor_key="host",
    )


def sample_command_result() -> CommandResult:
    return CommandResult(
        command_id=COMMAND_ID,
        accepted=True,
        revision=1,
        event_ids=(sample_events()[0].event_id,),
        error_code=None,
    )


def sample_trace() -> DMTraceRecord:
    return DMTraceRecord(
        trace_id=TRACE_ID,
        intent_id=INTENT_ID,
        template_variant_id="phase-notice-v1",
        catalog_version="s4-template-v1",
        source_event_ids=(sample_events()[0].event_id,),
        channel="public",
        audience_seat_ids=(),
        admission_status="admitted",
        suppress_reason=None,
        elapsed_ms=12,
    )


def sample_runtime() -> PersistedRoomRuntime:
    return PersistedRoomRuntime(
        outbox_seq=3,
        domain_to_transport={1: 1, 2: 3},
        completed_domain_seqs=(1, 2),
        processed_announcement_seq=2,
        published_message_ids=(PUBLISHED_MESSAGE_ID, SECOND_PUBLISHED_MESSAGE_ID),
        next_domain_seq=4,
        recovery_epoch=2,
        discarded_command_tombstones=(sample_command_key(),),
    )


def sample_snapshot() -> PersistedSnapshot:
    return PersistedSnapshot(
        snapshot_id=SNAPSHOT_ID,
        room_id=ROOM_ID,
        revision=1,
        reason=SnapshotReason.PRE_CORRECTION,
        state=sample_state(),
        event_count=1,
        created_at=CREATED_AT,
    )


def sample_before_state() -> GameState:
    return GameState(
        room_id=ROOM_ID,
        seed=7,
        revision=0,
        event_count=0,
        event_log_digest="0" * 64,
        phase=Phase.LOBBY,
        day=0,
    )


def sample_audit() -> PersistedRecoveryAudit:
    return PersistedRecoveryAudit(
        record_id=AUDIT_ID,
        room_id=ROOM_ID,
        command_id=COMMAND_ID,
        status="APPLIED",
        patch_type="SET_ALIVE",
        before_revision=0,
        after_revision=1,
        before_state=sample_before_state(),
        after_state=sample_state(),
        diff={"SET_ALIVE": {"seat_id": 1, "before": True, "after": False}},
        reason="fix an accidental death",
        created_at=CREATED_AT,
    )


def test_sqlite_store_round_trips_room_token_state_and_events(tmp_path: Path) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_token(sample_persisted_token())
    store.save_core(sample_room_id(), sample_state(), sample_events())
    store.close()

    reopened = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    reopened.migrate()
    assert reopened.load_rooms()[0].room_code == "AGT001"
    assert reopened.load_tokens()[0].actor_type == "host"
    assert reopened.load_core(sample_room_id())[0].revision == 1


def test_sqlite_store_rejects_corrupt_state_json(tmp_path: Path) -> None:
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_core(ROOM_ID, sample_state(), sample_events())
    store.close()

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE rooms SET state_json = ? WHERE room_id = ?",
        ("{not-json", str(ROOM_ID)),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteRoomStore(database_path)
    reopened.migrate()
    with pytest.raises(PersistenceCorruptionError):
        reopened.load_core(ROOM_ID)


def test_load_core_rejects_room_columns_that_disagree_with_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_core(ROOM_ID, sample_state(), sample_events())
    store.close()

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE rooms SET event_count = 99 WHERE room_id = ?",
        (str(ROOM_ID),),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteRoomStore(database_path)
    reopened.migrate()
    with pytest.raises(PersistenceCorruptionError):
        reopened.load_core(ROOM_ID)


def test_sqlite_store_never_persists_raw_tokens(tmp_path: Path) -> None:
    raw_token = "raw-host-token-that-must-never-be-persisted"
    raw_token_digest = hashlib.sha256(raw_token.encode()).hexdigest()
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_token(
        PersistedToken(
            token_digest=raw_token_digest,
            room_id=ROOM_ID,
            actor_type="host",
            issued_at=CREATED_AT,
            expires_at=EXPIRES_AT,
        )
    )
    store.close()

    database_bytes = database_path.read_bytes()
    assert raw_token.encode() not in database_bytes
    assert raw_token_digest.encode() in database_bytes


def test_save_core_appends_events_in_ordinal_order_and_rejects_duplicates(
    tmp_path: Path,
) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())
    first_events = sample_events()
    store.save_core(ROOM_ID, sample_state(), first_events)

    appended_events = (sample_appended_event(),)
    combined_events = (*first_events, *appended_events)
    store.save_core(
        ROOM_ID,
        state_for_events(combined_events, revision=2),
        appended_events,
    )

    loaded_state, loaded_events = store.load_core(ROOM_ID)
    assert loaded_state.revision == 2
    assert tuple(event.event_id for event in loaded_events) == tuple(
        event.event_id for event in combined_events
    )
    connection = sqlite3.connect(tmp_path / "rooms.sqlite3")
    ordinals = connection.execute(
        """
        SELECT event_ordinal
        FROM room_events
        WHERE room_id = ?
        ORDER BY event_ordinal
        """,
        (str(ROOM_ID),),
    ).fetchall()
    assert tuple(row[0] for row in ordinals) == (0, 1)
    connection.close()

    with pytest.raises(PersistenceConflictError):
        store.save_core(
            ROOM_ID,
            state_for_events(combined_events, revision=2),
            appended_events,
        )


def test_load_core_rejects_non_contiguous_event_ordinals(tmp_path: Path) -> None:
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_core(ROOM_ID, sample_state(), sample_events())
    store.close()

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE room_events SET event_ordinal = 7 WHERE room_id = ?",
        (str(ROOM_ID),),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteRoomStore(database_path)
    reopened.migrate()
    with pytest.raises(PersistenceCorruptionError):
        reopened.load_core(ROOM_ID)


def test_deleting_room_cannot_erase_event_history(tmp_path: Path) -> None:
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_core(ROOM_ID, sample_state(), sample_events())
    store.close()

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        "DELETE FROM rooms WHERE room_id = ?",
        (str(ROOM_ID),),
    )
    connection.commit()
    event_count = connection.execute(
        "SELECT COUNT(*) FROM room_events WHERE room_id = ?",
        (str(ROOM_ID),),
    ).fetchone()
    connection.close()

    assert event_count == (len(sample_events()),)


def test_delete_room_removes_owned_state_but_retains_events(tmp_path: Path) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_token(sample_persisted_token())
    store.save_core(ROOM_ID, sample_state(), sample_events())
    store.save_room_runtime(ROOM_ID, sample_runtime())

    store.delete_room(ROOM_ID)

    assert store.load_rooms() == ()
    assert store.load_tokens() == ()
    with pytest.raises(PersistenceNotFoundError):
        store.load_room_runtime(ROOM_ID)
    connection = sqlite3.connect(tmp_path / "rooms.sqlite3")
    event_count = connection.execute(
        "SELECT COUNT(*) FROM room_events WHERE room_id = ?",
        (str(ROOM_ID),),
    ).fetchone()
    connection.close()
    assert event_count == (len(sample_events()),)
    store.close()


def test_save_core_validates_complete_history_digest(tmp_path: Path) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())
    corrupt_state = sample_state().model_copy(update={"event_log_digest": "b" * 64})

    with pytest.raises(PersistenceConflictError):
        store.save_core(ROOM_ID, corrupt_state, sample_events())


def test_transaction_rolls_back_all_store_api_mutations(tmp_path: Path) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())

    with pytest.raises(RuntimeError), store.transaction():
        store.save_room(sample_persisted_room().model_copy(update={"seed": 99}))
        store.append_recovery_audit(sample_audit())
        raise RuntimeError("force rollback")

    assert store.load_rooms() == (sample_persisted_room(),)
    assert store.load_recovery_audit(ROOM_ID) == ()


def test_nested_write_conflict_rolls_back_without_aborting_outer_transaction(
    tmp_path: Path,
) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())
    mismatched_key = CommandDedupeKey(
        command_id=SECOND_COMMAND_ID,
        actor_type="host",
        actor_key="host",
    )

    with store.transaction(), pytest.raises(PersistenceConflictError):
        store.save_command_results(
            ROOM_ID,
            {
                sample_command_key(): sample_command_result(),
                mismatched_key: sample_command_result(),
            },
        )

    assert store.load_command_results(ROOM_ID) == {}


def test_command_trace_runtime_snapshot_and_audit_round_trip(
    tmp_path: Path,
) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())
    store.save_core(ROOM_ID, sample_state(), sample_events())
    command_key = sample_command_key()
    command_result = sample_command_result()
    trace = sample_trace()
    transport_trace = trace.model_copy(update={"trace_id": TRANSPORT_TRACE_ID})
    runtime = sample_runtime()
    snapshot = sample_snapshot()
    audit = sample_audit()
    store.save_command_results(ROOM_ID, {command_key: command_result})
    store.save_dm_trace(ROOM_ID, "DM_TRACE", trace)
    store.save_dm_trace(ROOM_ID, "DM_TRANSPORT_TRACE", transport_trace)
    store.save_room_runtime(ROOM_ID, runtime)
    store.save_snapshot(snapshot)
    store.append_recovery_audit(audit)
    store.close()

    reopened = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    reopened.migrate()
    assert reopened.load_command_results(ROOM_ID) == {
        command_key: command_result,
    }
    assert reopened.load_dm_traces(ROOM_ID, "DM_TRACE") == (trace,)
    assert reopened.load_dm_traces(ROOM_ID, "DM_TRANSPORT_TRACE") == (transport_trace,)
    assert reopened.load_room_runtime(ROOM_ID) == runtime
    assert reopened.load_snapshots(ROOM_ID) == (snapshot,)
    assert reopened.load_snapshot(SNAPSHOT_ID) == snapshot
    assert reopened.load_latest_pre_correction_snapshot(ROOM_ID) == snapshot
    assert reopened.load_recovery_audit(ROOM_ID) == (audit,)


def test_recovery_audit_is_append_only(tmp_path: Path) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(sample_persisted_room())
    store.append_recovery_audit(sample_audit())

    with pytest.raises(PersistenceConflictError):
        store.append_recovery_audit(sample_audit())


def test_load_recovery_audit_rejects_malformed_diff_json(tmp_path: Path) -> None:
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    store.save_room(sample_persisted_room())
    store.append_recovery_audit(sample_audit())
    store.close()

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE host_recovery_audit SET diff_json = ? WHERE record_id = ?",
        ("{not-json", str(AUDIT_ID)),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteRoomStore(database_path)
    reopened.migrate()
    with pytest.raises(PersistenceCorruptionError):
        reopened.load_recovery_audit(ROOM_ID)


def test_command_dedupe_key_requires_non_empty_actor_key() -> None:
    with pytest.raises(ValueError):
        CommandDedupeKey(command_id=COMMAND_ID, actor_type="host", actor_key="")


def test_build_production_registry_uses_env_database_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "nested" / "rooms.sqlite3"
    monkeypatch.setenv("WEREWOLF_DM_DB_PATH", str(database_path))

    registry = runtime.build_production_registry()
    created = registry.create_room(timedelta(hours=1))
    assert database_path.exists()
    assert registry.store is not None
    registry.store.close()

    reopened = SQLiteRoomStore(database_path)
    reopened.migrate()
    assert reopened.load_rooms()[0].room_id == created.room_id
    reopened.close()


def test_default_database_path_is_project_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEREWOLF_DM_DB_PATH", raising=False)
    expected = Path(runtime.__file__).resolve().parents[4] / "data" / "werewolf_dm.sqlite3"
    assert runtime._default_database_path() == expected
