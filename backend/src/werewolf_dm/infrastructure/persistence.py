from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_serializer, model_validator
from pydantic_core import to_jsonable_python

from werewolf_dm.application.dm_contracts import DMTraceRecord
from werewolf_dm.domain.contracts import CommandResult, DomainEvent
from werewolf_dm.domain.model import GameState, StrictModel, freeze_json_mapping
from werewolf_dm.domain.replay import event_log_digest

DMTraceKind = Literal["DM_TRACE", "DM_TRANSPORT_TRACE"]
SnapshotReason = Literal["PAUSED", "PRE_CORRECTION", "PHASE_START", "GAME_END"]


class PersistenceError(RuntimeError):
    """Base error for persistence failures."""


class PersistenceCorruptionError(PersistenceError):
    """Persisted data is malformed or internally inconsistent."""


class PersistenceConflictError(PersistenceError):
    """A mutation conflicts with already persisted append-only data."""


class PersistenceNotFoundError(PersistenceError):
    """A requested persisted record does not exist."""


class CommandDedupeKey(StrictModel):
    command_id: UUID
    actor_type: Literal["seat", "host"]
    actor_key: str = Field(min_length=1)


class PersistedDMTrace(StrictModel):
    trace_kind: DMTraceKind
    trace: DMTraceRecord


class PersistedAdmission(StrictModel):
    domain_seq: int = Field(ge=1)
    message_id: UUID
    transport_seq: int = Field(ge=0)
    recovery_epoch: int = Field(ge=0)


class PersistedPublication(StrictModel):
    transport_seq: int = Field(ge=0)
    message_id: UUID
    recovery_epoch: int = Field(ge=0)


class PersistedRoom(StrictModel):
    room_code: str = Field(min_length=1)
    room_id: UUID
    seed: int = Field(ge=0)
    rulepack_version: Literal["1.1.0"]
    expires_at: datetime
    last_activity_at: datetime


class PersistedToken(StrictModel):
    token_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    room_id: UUID
    actor_type: Literal["seat", "host", "display"]
    seat_id: int | None = Field(default=None, ge=1, le=6)
    session_id: UUID | None = None
    issued_at: datetime
    expires_at: datetime
    revoked: bool = False

    @model_validator(mode="after")
    def validate_actor_shape(self) -> Self:
        if self.actor_type == "seat":
            if self.seat_id is None or self.session_id is not None:
                raise ValueError("seat token requires seat_id and no session_id")
        elif self.actor_type == "host":
            if self.seat_id is not None or self.session_id is not None:
                raise ValueError("host token cannot carry seat_id or session_id")
        elif self.seat_id is not None or self.session_id is None:
            raise ValueError("display token requires session_id and no seat_id")
        return self


class PersistedRoomRuntime(StrictModel):
    outbox_seq: int = Field(ge=0)
    domain_to_transport: Mapping[int, int]
    completed_domain_seqs: tuple[int, ...]
    processed_announcement_seq: int = Field(ge=0)
    published_message_ids: tuple[UUID, ...]
    next_domain_seq: int = Field(ge=0)
    recovery_epoch: int = Field(ge=0)
    discarded_command_tombstones: tuple[CommandDedupeKey, ...]

    @model_validator(mode="after")
    def freeze_runtime(self) -> Self:
        if any(domain_seq < 1 for domain_seq in self.domain_to_transport):
            raise ValueError("domain_to_transport keys must be positive")
        transport_seqs = tuple(self.domain_to_transport.values())
        if any(transport_seq < 1 for transport_seq in transport_seqs):
            raise ValueError("domain_to_transport values must be positive")
        if len(set(transport_seqs)) != len(transport_seqs):
            raise ValueError("domain_to_transport values must be unique")
        if transport_seqs and max(transport_seqs) > self.outbox_seq:
            raise ValueError("outbox_seq must cover every transport mapping")
        if any(domain_seq < 1 for domain_seq in self.completed_domain_seqs):
            raise ValueError("completed domain sequences must be positive")
        if len(set(self.completed_domain_seqs)) != len(self.completed_domain_seqs):
            raise ValueError("completed domain sequences must be unique")
        if not set(self.domain_to_transport).issubset(self.completed_domain_seqs):
            raise ValueError("mapped domain sequences must be completed")
        latest_completed = max(self.completed_domain_seqs, default=0)
        if self.processed_announcement_seq != latest_completed:
            raise ValueError("processed announcement sequence must match completed state")
        if len(self.domain_to_transport) != len(self.published_message_ids):
            raise ValueError("published message ids must match transport mapping")
        if len(set(self.published_message_ids)) != len(self.published_message_ids):
            raise ValueError("published message ids must be unique")
        if len(set(self.discarded_command_tombstones)) != len(self.discarded_command_tombstones):
            raise ValueError("discarded command tombstones must be unique")
        object.__setattr__(
            self,
            "domain_to_transport",
            MappingProxyType(dict(self.domain_to_transport)),
        )
        object.__setattr__(
            self,
            "completed_domain_seqs",
            tuple(sorted(self.completed_domain_seqs)),
        )
        object.__setattr__(
            self,
            "published_message_ids",
            tuple(sorted(self.published_message_ids, key=str)),
        )
        return self

    @field_serializer("domain_to_transport")
    def serialize_domain_to_transport(
        self,
        mapping: Mapping[int, int],
    ) -> dict[str, int]:
        return {str(key): value for key, value in mapping.items()}


class PersistedSnapshot(StrictModel):
    snapshot_id: UUID
    room_id: UUID
    revision: int = Field(ge=0)
    reason: SnapshotReason
    state: GameState
    event_count: int = Field(ge=0)
    created_at: datetime

    @model_validator(mode="after")
    def validate_state_identity(self) -> Self:
        if self.state.room_id != self.room_id:
            raise ValueError("snapshot state must belong to the snapshot room")
        if self.state.revision != self.revision:
            raise ValueError("snapshot revision must match its state revision")
        if self.state.event_count != self.event_count:
            raise ValueError("snapshot event count must match its state event count")
        return self


class PersistedRecoveryAudit(StrictModel):
    record_id: UUID
    room_id: UUID
    command_id: UUID
    status: Literal["APPLIED", "REJECTED"]
    patch_type: str = Field(min_length=1)
    before_revision: int = Field(ge=0)
    after_revision: int | None = Field(default=None, ge=0)
    before_state: GameState | None = None
    after_state: GameState | None = None
    diff: Mapping[str, JsonValue] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_audit_state(self) -> Self:
        if self.status == "APPLIED":
            if self.after_revision is None or self.after_state is None:
                raise ValueError("applied recovery audit requires after state")
        elif self.after_revision != self.before_revision or self.after_state is not None:
            raise ValueError("rejected recovery audit cannot advance revision or state")
        for state in (self.before_state, self.after_state):
            if state is not None and state.room_id != self.room_id:
                raise ValueError("recovery audit state must belong to the audit room")
        object.__setattr__(self, "diff", freeze_json_mapping(self.diff))
        return self

    @field_serializer("diff")
    def serialize_diff(self, diff: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], _thaw_json_value(diff))


ModelT = TypeVar("ModelT", bound=BaseModel)

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS rooms (
        room_code TEXT PRIMARY KEY,
        room_id TEXT NOT NULL UNIQUE,
        seed INTEGER NOT NULL,
        rulepack_version TEXT NOT NULL,
        state_json TEXT,
        revision INTEGER,
        event_count INTEGER,
        event_log_digest TEXT,
        expires_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS room_events (
        room_id TEXT NOT NULL,
        event_ordinal INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        event_id TEXT NOT NULL,
        event_json TEXT NOT NULL,
        PRIMARY KEY (room_id, event_ordinal),
        UNIQUE (room_id, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS command_results (
        room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
        command_id TEXT NOT NULL,
        actor_type TEXT NOT NULL CHECK (actor_type IN ('seat', 'host')),
        actor_key TEXT NOT NULL CHECK (length(actor_key) > 0),
        result_json TEXT NOT NULL,
        PRIMARY KEY (room_id, command_id, actor_type, actor_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dm_traces (
        room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
        trace_id TEXT NOT NULL,
        trace_kind TEXT NOT NULL CHECK (trace_kind IN ('DM_TRACE', 'DM_TRANSPORT_TRACE')),
        trace_json TEXT NOT NULL,
        PRIMARY KEY (room_id, trace_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS room_runtime (
        room_id TEXT PRIMARY KEY REFERENCES rooms(room_id) ON DELETE CASCADE,
        outbox_seq INTEGER NOT NULL,
        next_domain_seq INTEGER NOT NULL,
        recovery_epoch INTEGER NOT NULL,
        discarded_command_keys_json TEXT NOT NULL,
        runtime_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS room_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
        revision INTEGER NOT NULL,
        reason TEXT NOT NULL CHECK (
            reason IN ('PAUSED', 'PRE_CORRECTION', 'PHASE_START', 'GAME_END')
        ),
        state_json TEXT NOT NULL,
        event_count INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS room_tokens (
        token_digest TEXT PRIMARY KEY,
        room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
        actor_type TEXT NOT NULL CHECK (actor_type IN ('seat', 'host', 'display')),
        seat_id INTEGER,
        session_id TEXT,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked INTEGER NOT NULL CHECK (revoked IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS host_recovery_audit (
        record_id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
        command_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('APPLIED', 'REJECTED')),
        patch_type TEXT NOT NULL,
        before_revision INTEGER NOT NULL,
        after_revision INTEGER,
        before_state_json TEXT,
        after_state_json TEXT,
        diff_json TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS room_tokens_room_id_idx
    ON room_tokens(room_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS room_snapshots_room_reason_revision_idx
    ON room_snapshots(room_id, reason, revision DESC, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS host_recovery_audit_room_idx
    ON host_recovery_audit(room_id, created_at)
    """,
)


class SQLiteRoomStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._connection: sqlite3.Connection | None = sqlite3.connect(str(self._path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._transaction_depth = 0

    def migrate(self) -> None:
        with self.transaction() as connection:
            for statement in _SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 1")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        outermost = self._transaction_depth == 0
        if outermost:
            connection.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield connection
        except BaseException:
            self._transaction_depth -= 1
            if outermost:
                connection.rollback()
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                try:
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

    def save_room(self, room: PersistedRoom) -> None:
        self._write(lambda connection: self._save_room(connection, room))

    def load_rooms(self) -> tuple[PersistedRoom, ...]:
        rows = (
            self._require_connection()
            .execute(
                """
            SELECT room_code, room_id, seed, rulepack_version, expires_at, last_activity_at
            FROM rooms
            ORDER BY room_code
            """
            )
            .fetchall()
        )
        return tuple(self._load_room_row(row) for row in rows)

    def save_token(self, token: PersistedToken) -> None:
        self._write(lambda connection: self._save_token(connection, token))

    def load_tokens(self) -> tuple[PersistedToken, ...]:
        rows = (
            self._require_connection()
            .execute(
                """
            SELECT token_digest, room_id, actor_type, seat_id, session_id,
                   issued_at, expires_at, revoked
            FROM room_tokens
            ORDER BY token_digest
            """
            )
            .fetchall()
        )
        return tuple(self._load_token_row(row) for row in rows)

    def delete_room(self, room_id: UUID) -> None:
        self._write(lambda connection: self._delete_room(connection, room_id))

    def save_core(
        self,
        room_id: UUID,
        state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> None:
        self._write(lambda connection: self._save_core(connection, room_id, state, events))

    def load_core(self, room_id: UUID) -> tuple[GameState, tuple[DomainEvent, ...]]:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT state_json, revision, event_count, event_log_digest
            FROM rooms
            WHERE room_id = ?
            """,
            (str(room_id),),
        ).fetchone()
        if row is None:
            raise PersistenceNotFoundError(f"room {room_id} does not exist")
        if row["state_json"] is None:
            raise PersistenceNotFoundError(f"room {room_id} has no persisted core")
        state = self._load_model(GameState, row["state_json"], "game state")
        if (
            row["revision"] != state.revision
            or row["event_count"] != state.event_count
            or row["event_log_digest"] != state.event_log_digest
        ):
            raise PersistenceCorruptionError("persisted room columns disagree with state")
        events = self._load_events(connection, room_id)
        self._validate_core(room_id, state, events)
        return state, events

    def save_command_results(
        self,
        room_id: UUID,
        results: Mapping[CommandDedupeKey, CommandResult],
    ) -> None:
        self._write(lambda connection: self._save_command_results(connection, room_id, results))

    def load_command_results(
        self,
        room_id: UUID,
    ) -> dict[CommandDedupeKey, CommandResult]:
        rows = (
            self._require_connection()
            .execute(
                """
            SELECT command_id, actor_type, actor_key, result_json
            FROM command_results
            WHERE room_id = ?
            ORDER BY command_id, actor_type, actor_key
            """,
                (str(room_id),),
            )
            .fetchall()
        )
        loaded: dict[CommandDedupeKey, CommandResult] = {}
        for row in rows:
            key = CommandDedupeKey(
                command_id=_load_uuid(row["command_id"], "command id"),
                actor_type=row["actor_type"],
                actor_key=row["actor_key"],
            )
            loaded[key] = self._load_model(CommandResult, row["result_json"], "command result")
        return loaded

    def save_dm_trace(
        self,
        room_id: UUID,
        trace_kind: DMTraceKind,
        trace: DMTraceRecord,
    ) -> None:
        self._write(lambda connection: self._save_dm_trace(connection, room_id, trace_kind, trace))

    def load_dm_traces(
        self,
        room_id: UUID,
        trace_kind: DMTraceKind,
    ) -> tuple[DMTraceRecord, ...]:
        rows = (
            self._require_connection()
            .execute(
                """
            SELECT trace_json
            FROM dm_traces
            WHERE room_id = ? AND trace_kind = ?
            ORDER BY rowid
            """,
                (str(room_id), trace_kind),
            )
            .fetchall()
        )
        return tuple(self._load_model(DMTraceRecord, row["trace_json"], "DM trace") for row in rows)

    def save_room_runtime(
        self,
        room_id: UUID,
        runtime: PersistedRoomRuntime,
    ) -> None:
        self._write(lambda connection: self._save_room_runtime(connection, room_id, runtime))

    def load_room_runtime(self, room_id: UUID) -> PersistedRoomRuntime:
        row = (
            self._require_connection()
            .execute(
                """
            SELECT runtime_json
            FROM room_runtime
            WHERE room_id = ?
            """,
                (str(room_id),),
            )
            .fetchone()
        )
        if row is None:
            raise PersistenceNotFoundError(f"room {room_id} has no persisted runtime")
        return self._load_model(PersistedRoomRuntime, row["runtime_json"], "room runtime")

    def save_snapshot(self, snapshot: PersistedSnapshot) -> None:
        self._write(lambda connection: self._save_snapshot(connection, snapshot))

    def load_snapshots(self, room_id: UUID) -> tuple[PersistedSnapshot, ...]:
        rows = (
            self._require_connection()
            .execute(
                """
            SELECT snapshot_id, room_id, revision, reason, state_json,
                   event_count, created_at
            FROM room_snapshots
            WHERE room_id = ?
            ORDER BY created_at, snapshot_id
            """,
                (str(room_id),),
            )
            .fetchall()
        )
        return tuple(self._load_snapshot_row(row) for row in rows)

    def load_latest_pre_correction_snapshot(
        self,
        room_id: UUID,
    ) -> PersistedSnapshot:
        row = (
            self._require_connection()
            .execute(
                """
            SELECT snapshot_id, room_id, revision, reason, state_json,
                   event_count, created_at
            FROM room_snapshots
            WHERE room_id = ? AND reason = 'PRE_CORRECTION'
            ORDER BY revision DESC, created_at DESC, snapshot_id DESC
            LIMIT 1
            """,
                (str(room_id),),
            )
            .fetchone()
        )
        if row is None:
            raise PersistenceNotFoundError(f"room {room_id} has no pre-correction snapshot")
        return self._load_snapshot_row(row)

    def load_snapshot(self, snapshot_id: UUID) -> PersistedSnapshot:
        row = (
            self._require_connection()
            .execute(
                """
            SELECT snapshot_id, room_id, revision, reason, state_json,
                   event_count, created_at
            FROM room_snapshots
            WHERE snapshot_id = ?
            """,
                (str(snapshot_id),),
            )
            .fetchone()
        )
        if row is None:
            raise PersistenceNotFoundError(f"snapshot {snapshot_id} does not exist")
        return self._load_snapshot_row(row)

    def append_recovery_audit(self, record: PersistedRecoveryAudit) -> None:
        self._write(lambda connection: self._append_recovery_audit(connection, record))

    def load_recovery_audit(
        self,
        room_id: UUID,
    ) -> tuple[PersistedRecoveryAudit, ...]:
        rows = (
            self._require_connection()
            .execute(
                """
            SELECT record_id, room_id, command_id, status, patch_type,
                   before_revision, after_revision, before_state_json,
                   after_state_json, diff_json, reason, created_at
            FROM host_recovery_audit
            WHERE room_id = ?
            ORDER BY created_at, record_id
            """,
                (str(room_id),),
            )
            .fetchall()
        )
        return tuple(self._load_recovery_audit_row(row) for row in rows)

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise PersistenceError("store is closed")
        return self._connection

    def _write(self, operation: Callable[[sqlite3.Connection], None]) -> None:
        connection = self._require_connection()
        if self._transaction_depth > 0:
            connection.execute("SAVEPOINT store_write")
            try:
                operation(connection)
            except BaseException:
                connection.execute("ROLLBACK TO SAVEPOINT store_write")
                connection.execute("RELEASE SAVEPOINT store_write")
                raise
            else:
                connection.execute("RELEASE SAVEPOINT store_write")
            return
        with self.transaction() as transaction:
            operation(transaction)

    def _save_room(
        self,
        connection: sqlite3.Connection,
        room: PersistedRoom,
    ) -> None:
        connection.execute(
            """
            INSERT INTO rooms (
                room_code, room_id, seed, rulepack_version, expires_at, last_activity_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET
                room_code = excluded.room_code,
                seed = excluded.seed,
                rulepack_version = excluded.rulepack_version,
                expires_at = excluded.expires_at,
                last_activity_at = excluded.last_activity_at
            """,
            (
                room.room_code,
                str(room.room_id),
                room.seed,
                room.rulepack_version,
                _dump_datetime(room.expires_at),
                _dump_datetime(room.last_activity_at),
            ),
        )

    def _load_room_row(self, row: sqlite3.Row) -> PersistedRoom:
        return PersistedRoom.model_validate(
            {
                "room_code": row["room_code"],
                "room_id": _load_uuid(row["room_id"], "room id"),
                "seed": row["seed"],
                "rulepack_version": row["rulepack_version"],
                "expires_at": _load_datetime(row["expires_at"], "room expiry"),
                "last_activity_at": _load_datetime(row["last_activity_at"], "room activity"),
            },
            strict=True,
        )

    def _save_token(
        self,
        connection: sqlite3.Connection,
        token: PersistedToken,
    ) -> None:
        connection.execute(
            """
            INSERT INTO room_tokens (
                token_digest, room_id, actor_type, seat_id, session_id,
                issued_at, expires_at, revoked
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_digest) DO UPDATE SET
                room_id = excluded.room_id,
                actor_type = excluded.actor_type,
                seat_id = excluded.seat_id,
                session_id = excluded.session_id,
                issued_at = excluded.issued_at,
                expires_at = excluded.expires_at,
                revoked = excluded.revoked
            """,
            (
                token.token_digest,
                str(token.room_id),
                token.actor_type,
                token.seat_id,
                str(token.session_id) if token.session_id is not None else None,
                _dump_datetime(token.issued_at),
                _dump_datetime(token.expires_at),
                int(token.revoked),
            ),
        )

    def _load_token_row(self, row: sqlite3.Row) -> PersistedToken:
        return PersistedToken.model_validate(
            {
                "token_digest": row["token_digest"],
                "room_id": _load_uuid(row["room_id"], "token room id"),
                "actor_type": row["actor_type"],
                "seat_id": row["seat_id"],
                "session_id": (
                    _load_uuid(row["session_id"], "token session id")
                    if row["session_id"] is not None
                    else None
                ),
                "issued_at": _load_datetime(row["issued_at"], "token issue time"),
                "expires_at": _load_datetime(row["expires_at"], "token expiry"),
                "revoked": bool(row["revoked"]),
            },
            strict=True,
        )

    def _delete_room(self, connection: sqlite3.Connection, room_id: UUID) -> None:
        connection.execute(
            """
            DELETE FROM rooms
            WHERE room_id = ?
            """,
            (str(room_id),),
        )

    def _save_core(
        self,
        connection: sqlite3.Connection,
        room_id: UUID,
        state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> None:
        room_row = connection.execute(
            "SELECT room_id FROM rooms WHERE room_id = ?",
            (str(room_id),),
        ).fetchone()
        if room_row is None:
            raise PersistenceNotFoundError(f"room {room_id} does not exist")

        existing_events = self._load_events(connection, room_id)
        existing_event_ids = {event.event_id for event in existing_events}
        if len(existing_event_ids) != len(existing_events):
            raise PersistenceCorruptionError("persisted event ids are not unique")
        if any(event.event_id in existing_event_ids for event in events):
            raise PersistenceConflictError("cannot append an existing event")
        if len({event.event_id for event in events}) != len(events):
            raise PersistenceConflictError("appended event ids must be unique")
        if any(event.room_id != room_id for event in events):
            raise PersistenceConflictError("appended events must belong to the room")

        combined_events = (*existing_events, *events)
        revisions = tuple(event.revision for event in combined_events)
        if revisions != tuple(sorted(revisions)):
            raise PersistenceConflictError("event revisions must be monotonic")
        self._validate_core(room_id, state, combined_events)

        updated = connection.execute(
            """
            UPDATE rooms
            SET state_json = ?,
                revision = ?,
                event_count = ?,
                event_log_digest = ?
            WHERE room_id = ?
            """,
            (
                _dump_model(state),
                state.revision,
                state.event_count,
                state.event_log_digest,
                str(room_id),
            ),
        )
        if updated.rowcount != 1:
            raise PersistenceNotFoundError(f"room {room_id} does not exist")

        first_ordinal = len(existing_events)
        for offset, event in enumerate(events):
            try:
                connection.execute(
                    """
                    INSERT INTO room_events (
                        room_id, event_ordinal, revision, event_id, event_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(room_id),
                        first_ordinal + offset,
                        event.revision,
                        str(event.event_id),
                        _dump_model(event),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceConflictError("event append conflicts") from exc

    def _load_events(
        self,
        connection: sqlite3.Connection,
        room_id: UUID,
    ) -> tuple[DomainEvent, ...]:
        rows = connection.execute(
            """
            SELECT event_ordinal, event_json
            FROM room_events
            WHERE room_id = ?
            ORDER BY event_ordinal
            """,
            (str(room_id),),
        ).fetchall()
        ordinals = tuple(row["event_ordinal"] for row in rows)
        if ordinals != tuple(range(len(rows))):
            raise PersistenceCorruptionError("event ordinals must be contiguous")
        return tuple(self._load_model(DomainEvent, row["event_json"], "room event") for row in rows)

    def _validate_core(
        self,
        room_id: UUID,
        state: GameState,
        events: tuple[DomainEvent, ...],
    ) -> None:
        if state.room_id != room_id:
            raise PersistenceConflictError("state must belong to the room")
        if any(event.room_id != room_id for event in events):
            raise PersistenceCorruptionError("event belongs to another room")
        if state.event_count != len(events):
            raise PersistenceConflictError("state event_count does not match history")
        if state.event_log_digest != event_log_digest(events):
            raise PersistenceConflictError("state event_log_digest does not match history")
        if events and state.revision != events[-1].revision:
            raise PersistenceConflictError("state revision does not match event history")
        if not events and state.revision != 0:
            raise PersistenceConflictError("empty event history requires revision zero")

    def _save_command_results(
        self,
        connection: sqlite3.Connection,
        room_id: UUID,
        results: Mapping[CommandDedupeKey, CommandResult],
    ) -> None:
        for key, result in results.items():
            if key.command_id != result.command_id:
                raise PersistenceConflictError("command dedupe key does not match result command")
            connection.execute(
                """
                INSERT INTO command_results (
                    room_id, command_id, actor_type, actor_key, result_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_id, command_id, actor_type, actor_key) DO UPDATE SET
                    result_json = excluded.result_json
                """,
                (
                    str(room_id),
                    str(key.command_id),
                    key.actor_type,
                    key.actor_key,
                    _dump_model(result),
                ),
            )

    def _save_dm_trace(
        self,
        connection: sqlite3.Connection,
        room_id: UUID,
        trace_kind: DMTraceKind,
        trace: DMTraceRecord,
    ) -> None:
        existing = connection.execute(
            """
            SELECT trace_kind
            FROM dm_traces
            WHERE room_id = ? AND trace_id = ?
            """,
            (str(room_id), str(trace.trace_id)),
        ).fetchone()
        if existing is not None and existing["trace_kind"] != trace_kind:
            raise PersistenceConflictError("trace kind cannot change for a trace id")
        connection.execute(
            """
            INSERT INTO dm_traces (room_id, trace_id, trace_kind, trace_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room_id, trace_id) DO UPDATE SET
                trace_json = excluded.trace_json
            """,
            (str(room_id), str(trace.trace_id), trace_kind, _dump_model(trace)),
        )

    def _save_room_runtime(
        self,
        connection: sqlite3.Connection,
        room_id: UUID,
        runtime: PersistedRoomRuntime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO room_runtime (
                room_id, outbox_seq, next_domain_seq, recovery_epoch,
                discarded_command_keys_json, runtime_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET
                outbox_seq = excluded.outbox_seq,
                next_domain_seq = excluded.next_domain_seq,
                recovery_epoch = excluded.recovery_epoch,
                discarded_command_keys_json = excluded.discarded_command_keys_json,
                runtime_json = excluded.runtime_json
            """,
            (
                str(room_id),
                runtime.outbox_seq,
                runtime.next_domain_seq,
                runtime.recovery_epoch,
                _dump_json_value(
                    [key.model_dump(mode="json") for key in runtime.discarded_command_tombstones]
                ),
                _dump_model(runtime),
            ),
        )

    def _save_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: PersistedSnapshot,
    ) -> None:
        connection.execute(
            """
            INSERT INTO room_snapshots (
                snapshot_id, room_id, revision, reason, state_json,
                event_count, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                revision = excluded.revision,
                reason = excluded.reason,
                state_json = excluded.state_json,
                event_count = excluded.event_count,
                created_at = excluded.created_at
            """,
            (
                str(snapshot.snapshot_id),
                str(snapshot.room_id),
                snapshot.revision,
                snapshot.reason,
                _dump_model(snapshot.state),
                snapshot.event_count,
                _dump_datetime(snapshot.created_at),
            ),
        )

    def _load_snapshot_row(self, row: sqlite3.Row) -> PersistedSnapshot:
        return PersistedSnapshot(
            snapshot_id=_load_uuid(row["snapshot_id"], "snapshot id"),
            room_id=_load_uuid(row["room_id"], "snapshot room id"),
            revision=row["revision"],
            reason=row["reason"],
            state=self._load_model(GameState, row["state_json"], "snapshot state"),
            event_count=row["event_count"],
            created_at=_load_datetime(row["created_at"], "snapshot creation time"),
        )

    def _append_recovery_audit(
        self,
        connection: sqlite3.Connection,
        record: PersistedRecoveryAudit,
    ) -> None:
        try:
            connection.execute(
                """
                INSERT INTO host_recovery_audit (
                    record_id, room_id, command_id, status, patch_type,
                    before_revision, after_revision, before_state_json,
                    after_state_json, diff_json, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.record_id),
                    str(record.room_id),
                    str(record.command_id),
                    record.status,
                    record.patch_type,
                    record.before_revision,
                    record.after_revision,
                    (_dump_model(record.before_state) if record.before_state is not None else None),
                    (_dump_model(record.after_state) if record.after_state is not None else None),
                    _dump_json_value(dict(record.diff)),
                    record.reason,
                    _dump_datetime(record.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflictError("recovery audit record already exists") from exc

    def _load_recovery_audit_row(
        self,
        row: sqlite3.Row,
    ) -> PersistedRecoveryAudit:
        try:
            return PersistedRecoveryAudit(
                record_id=_load_uuid(row["record_id"], "audit record id"),
                room_id=_load_uuid(row["room_id"], "audit room id"),
                command_id=_load_uuid(row["command_id"], "audit command id"),
                status=row["status"],
                patch_type=row["patch_type"],
                before_revision=row["before_revision"],
                after_revision=row["after_revision"],
                before_state=(
                    self._load_model(GameState, row["before_state_json"], "audit before state")
                    if row["before_state_json"] is not None
                    else None
                ),
                after_state=(
                    self._load_model(GameState, row["after_state_json"], "audit after state")
                    if row["after_state_json"] is not None
                    else None
                ),
                diff=_load_json_mapping(row["diff_json"], "recovery audit diff"),
                reason=row["reason"],
                created_at=_load_datetime(row["created_at"], "audit creation time"),
            )
        except PersistenceCorruptionError:
            raise
        except ValueError as exc:
            raise PersistenceCorruptionError("corrupt persisted recovery audit") from exc

    def _load_model(
        self,
        model_type: type[ModelT],
        payload: str,
        label: str,
    ) -> ModelT:
        try:
            return model_type.model_validate_json(payload, strict=True)
        except ValueError as exc:
            raise PersistenceCorruptionError(f"corrupt persisted {label}") from exc


def _dump_model(model: BaseModel) -> str:
    return _dump_json_value(model.model_dump(mode="json"))


def _dump_datetime(value: datetime) -> str:
    return cast(str, to_jsonable_python(value))


def _load_uuid(payload: object, label: str) -> UUID:
    try:
        return UUID(str(payload))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PersistenceCorruptionError(f"corrupt persisted {label}") from exc


def _load_datetime(payload: object, label: str) -> datetime:
    if not isinstance(payload, str):
        raise PersistenceCorruptionError(f"corrupt persisted {label}")
    try:
        return datetime.fromisoformat(payload)
    except ValueError as exc:
        raise PersistenceCorruptionError(f"corrupt persisted {label}") from exc


def _load_json_mapping(payload: object, label: str) -> Mapping[str, JsonValue]:
    if not isinstance(payload, str):
        raise PersistenceCorruptionError(f"corrupt persisted {label}")
    try:
        loaded = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise PersistenceCorruptionError(f"corrupt persisted {label}") from exc
    if not isinstance(loaded, dict) or any(not isinstance(key, str) for key in loaded):
        raise PersistenceCorruptionError(f"corrupt persisted {label}")
    return cast(Mapping[str, JsonValue], loaded)


def _dump_json_value(value: object) -> str:
    return json.dumps(
        to_jsonable_python(_thaw_json_value(value)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json_value(item) for item in value]
    return value
