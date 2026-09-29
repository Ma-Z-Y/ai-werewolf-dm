from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID

from pydantic import JsonValue

from werewolf_dm.application.core import DomainSeqAllocator, FrozenClock, GameCore
from werewolf_dm.domain.contracts import (
    EVENT_PAYLOAD_MODELS,
    AuthenticatedActor,
    CommandDedupeKey,
    CommandEnvelope,
    CommandErrorCode,
    CommandResult,
    DomainEvent,
    HostCorrectionAudit,
    HostForceTemplateCommand,
    HostPatchCommand,
    HostRewindAudit,
    HostRewindToSnapshotCommand,
    HostTemplateForcedAudit,
    HostVisibility,
    SnapshotReason,
    build_event,
)
from werewolf_dm.domain.enums import EventType, Phase
from werewolf_dm.domain.model import GameState
from werewolf_dm.domain.replay import event_log_digest, state_hash
from werewolf_dm.domain.state_machine import deterministic_uuid, validate_invariants
from werewolf_dm.domain.visibility import project_public_view, project_seat_view
from werewolf_dm.infrastructure.persistence import (
    PersistedRecoveryAudit,
    PersistedRoomRuntime,
    PersistedSnapshot,
    PersistenceNotFoundError,
    SQLiteRoomStore,
)


@dataclass(frozen=True, slots=True)
class RecoveryCommit:
    next_state: GameState
    events: tuple[DomainEvent, ...]
    command_result: CommandResult
    runtime: PersistedRoomRuntime
    snapshot: PersistedSnapshot | None
    audit: PersistedRecoveryAudit | None


class HostRecoveryService:
    def __init__(self, store: SQLiteRoomStore) -> None:
        self._store = store

    def snapshot(self, room_id: UUID, reason: str) -> UUID:
        reason_value = SnapshotReason(reason)
        state, _ = self._store.load_core(room_id)
        snapshot_id = deterministic_uuid(
            NAMESPACE_URL,
            room_id,
            state.revision,
            reason_value.value,
        )
        snapshot = self._snapshot_for_state(
            state,
            reason_value,
            snapshot_id=snapshot_id,
            created_at=datetime.now(UTC),
        )
        with self._store.transaction():
            self._store.save_snapshot(snapshot)
        return snapshot_id

    def stage_patch(
        self,
        *,
        room_id: UUID,
        core: GameCore,
        runtime: PersistedRoomRuntime,
        command: HostPatchCommand,
        actor: AuthenticatedActor,
        now: datetime,
        command_id: UUID,
        expected_revision: int,
    ) -> RecoveryCommit:
        actor = AuthenticatedActor.revalidate(actor)
        command = HostPatchCommand.revalidate(command)
        if not self._is_authorized_host(core.state, actor) or room_id != core.state.room_id:
            return self._rejected_commit(
                state=core.state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.patch.patch_type,
                error_code=CommandErrorCode.ACTOR_NOT_AUTHORIZED,
                now=now,
            )
        envelope = self._envelope(
            room_id=room_id,
            command=command,
            command_id=command_id,
            expected_revision=expected_revision,
            now=now,
        )
        allocator = DomainSeqAllocator(runtime.next_domain_seq)
        mutation = core.stage_submit(
            envelope,
            actor,
            next_domain_seq=allocator,
            discarded_command_tombstones=runtime.discarded_command_tombstones,
        )
        for reservation in mutation.seq_reservations:
            allocator.commit(reservation)
        next_runtime = self._replace_runtime(
            runtime,
            next_domain_seq=allocator.current(),
        )
        result = mutation.command_result
        applied = result.accepted and bool(mutation.appended_events)
        if not applied:
            if result.accepted:
                return RecoveryCommit(
                    next_state=mutation.next_state,
                    events=mutation.appended_events,
                    command_result=result,
                    runtime=next_runtime,
                    snapshot=None,
                    audit=None,
                )
            return self._rejected_commit(
                state=core.state,
                runtime=next_runtime,
                command_id=command_id,
                patch_type=command.patch.patch_type,
                error_code=result.error_code or CommandErrorCode.INVALID_TARGET,
                now=now,
            )

        snapshot = self._snapshot_for_state(
            core.state,
            SnapshotReason.PRE_CORRECTION,
            snapshot_id=self._snapshot_id_for_command(
                command_id,
                SnapshotReason.PRE_CORRECTION,
            ),
            created_at=now,
        )
        diff, reason = self._correction_details(mutation.appended_events)
        audit = PersistedRecoveryAudit(
            record_id=self._audit_id(
                core.state.room_id,
                command_id,
                command.patch.patch_type,
                "APPLIED",
            ),
            room_id=core.state.room_id,
            command_id=command_id,
            status="APPLIED",
            patch_type=command.patch.patch_type,
            before_revision=core.state.revision,
            after_revision=mutation.next_state.revision,
            before_state=core.state,
            after_state=mutation.next_state,
            diff=diff,
            reason=reason,
            created_at=now,
        )
        return RecoveryCommit(
            next_state=mutation.next_state,
            events=mutation.appended_events,
            command_result=result,
            runtime=next_runtime,
            snapshot=snapshot,
            audit=audit,
        )

    def stage_rewind(
        self,
        *,
        core: GameCore,
        runtime: PersistedRoomRuntime,
        command: HostRewindToSnapshotCommand,
        actor: AuthenticatedActor,
        now: datetime,
        command_id: UUID,
        expected_revision: int,
    ) -> RecoveryCommit:
        actor = AuthenticatedActor.revalidate(actor)
        command = HostRewindToSnapshotCommand.revalidate(command)
        if not self._is_authorized_host(core.state, actor):
            return self._rejected_commit(
                state=core.state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=CommandErrorCode.ACTOR_NOT_AUTHORIZED,
                now=now,
            )
        cached = core.command_dedupe_cache.get((command_id, "host", None))
        if cached is not None:
            return RecoveryCommit(
                next_state=core.state,
                events=(),
                command_result=cached,
                runtime=runtime,
                snapshot=None,
                audit=None,
            )
        if not self._can_recover(core.state, actor, expected_revision):
            return self._rejected_commit(
                state=core.state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=self._recovery_error(core.state, actor, expected_revision),
                now=now,
            )
        try:
            snapshot = self._store.load_snapshot(command.snapshot_id)
        except PersistenceNotFoundError:
            return self._rejected_commit(
                state=core.state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=CommandErrorCode.INVALID_TARGET,
                now=now,
            )
        if (
            snapshot.room_id != core.state.room_id
            or snapshot.reason != SnapshotReason.PRE_CORRECTION
        ):
            return self._rejected_commit(
                state=core.state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=CommandErrorCode.INVALID_TARGET,
                now=now,
            )
        try:
            latest = self._store.load_latest_pre_correction_snapshot(core.state.room_id)
        except PersistenceNotFoundError:
            latest = None
        if latest is None or latest.snapshot_id != snapshot.snapshot_id:
            return self._rejected_commit(
                state=core.state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=CommandErrorCode.INVALID_TARGET,
                now=now,
            )

        next_revision = core.state.revision + 1
        rewind_event = build_event(
            cause_id=command_id,
            event_ordinal=len(core.events),
            room_id=core.state.room_id,
            revision=next_revision,
            event_type=EventType.HOST_REWIND_APPLIED,
            visibility=HostVisibility(),
            payload=HostRewindAudit(
                command_id=command_id,
                snapshot_id=snapshot.snapshot_id,
                from_revision=core.state.revision,
                to_revision=snapshot.revision,
                restored_state_digest=state_hash(snapshot.state),
            ),
            created_at=now,
        )
        events = (*core.events, rewind_event)
        restored_state = snapshot.state.model_copy(
            update={
                "revision": next_revision,
                "event_count": len(events),
                "event_log_digest": event_log_digest(events),
            }
        )
        validate_invariants(restored_state)
        project_public_view(restored_state)
        for seat_id in range(1, 7):
            project_seat_view(
                restored_state,
                seat_id,
                AuthenticatedActor(
                    actor_type="seat",
                    seat_id=seat_id,
                    room_id=core.state.room_id,
                ),
            )

        discarded = self._discarded_commands(core, snapshot.revision)
        tombstones = tuple(dict.fromkeys((*runtime.discarded_command_tombstones, *discarded)))
        restored_next_domain_seq = restored_state.outbox[-1].seq + 1 if restored_state.outbox else 1
        next_domain_seq = max(runtime.next_domain_seq, restored_next_domain_seq) + 1
        next_runtime = PersistedRoomRuntime(
            outbox_seq=runtime.outbox_seq,
            domain_to_transport={},
            completed_domain_seqs=(),
            processed_announcement_seq=0,
            published_message_ids=(),
            next_domain_seq=next_domain_seq,
            recovery_epoch=runtime.recovery_epoch + 1,
            discarded_command_tombstones=tombstones,
        )
        snapshot_before_rewind = self._snapshot_for_state(
            core.state,
            SnapshotReason.PRE_CORRECTION,
            snapshot_id=self._snapshot_id_for_command(
                command_id,
                SnapshotReason.PRE_CORRECTION,
            ),
            created_at=now,
        )
        command_result = CommandResult(
            command_id=command_id,
            accepted=True,
            revision=next_revision,
            event_ids=(rewind_event.event_id,),
            error_code=None,
        )
        audit = PersistedRecoveryAudit(
            record_id=self._audit_id(
                core.state.room_id,
                command_id,
                command.command_type.value,
                "APPLIED",
            ),
            room_id=core.state.room_id,
            command_id=command_id,
            status="APPLIED",
            patch_type=command.command_type.value,
            before_revision=core.state.revision,
            after_revision=next_revision,
            before_state=core.state,
            after_state=restored_state,
            diff={
                "snapshot_id": str(snapshot.snapshot_id),
                "from_revision": core.state.revision,
                "to_revision": snapshot.revision,
                "recovery_epoch": next_runtime.recovery_epoch,
            },
            reason="host rewind",
            created_at=now,
        )
        return RecoveryCommit(
            next_state=restored_state,
            events=(rewind_event,),
            command_result=command_result,
            runtime=next_runtime,
            snapshot=snapshot_before_rewind,
            audit=audit,
        )

    def stage_force_template(
        self,
        *,
        core: GameCore,
        runtime: PersistedRoomRuntime,
        command: HostForceTemplateCommand,
        actor: AuthenticatedActor,
        now: datetime,
        command_id: UUID,
        expected_revision: int,
    ) -> RecoveryCommit:
        actor = AuthenticatedActor.revalidate(actor)
        command = HostForceTemplateCommand.revalidate(command)
        state = core.state
        if not self._is_authorized_host(state, actor):
            return self._rejected_commit(
                state=state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=CommandErrorCode.ACTOR_NOT_AUTHORIZED,
                now=now,
            )
        record_id = self._audit_id(
            state.room_id,
            command_id,
            command.command_type.value,
            "APPLIED",
        )
        existing_record = next(
            (
                record
                for record in self._store.load_recovery_audit(state.room_id)
                if record.record_id == record_id
            ),
            None,
        )
        if existing_record is not None:
            return RecoveryCommit(
                next_state=state,
                events=(),
                command_result=CommandResult(
                    command_id=command_id,
                    accepted=True,
                    revision=(
                        state.revision
                        if existing_record.after_revision is None
                        else existing_record.after_revision
                    ),
                    event_ids=(),
                    error_code=None,
                ),
                runtime=runtime,
                snapshot=None,
                audit=None,
            )
        if state.phase is Phase.GAME_END:
            return self._rejected_commit(
                state=state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=CommandErrorCode.GAME_ENDED,
                now=now,
            )
        if expected_revision != state.revision:
            return self._rejected_commit(
                state=state,
                runtime=runtime,
                command_id=command_id,
                patch_type=command.command_type.value,
                error_code=CommandErrorCode.REVISION_CONFLICT,
                now=now,
            )
        canonical = HostTemplateForcedAudit.revalidate(
            HostTemplateForcedAudit(
                command_id=command_id,
                revision=state.revision,
            )
        )
        audit = PersistedRecoveryAudit(
            record_id=record_id,
            room_id=state.room_id,
            command_id=canonical.command_id,
            status="APPLIED",
            patch_type=command.command_type.value,
            before_revision=canonical.revision,
            after_revision=canonical.revision,
            before_state=state,
            after_state=state,
            diff={},
            reason="host force template",
            created_at=now,
        )
        return RecoveryCommit(
            next_state=state,
            events=(),
            command_result=CommandResult(
                command_id=command_id,
                accepted=True,
                revision=state.revision,
                event_ids=(),
                error_code=None,
            ),
            runtime=runtime,
            snapshot=None,
            audit=audit,
        )

    def persist_commit(
        self,
        connection: sqlite3.Connection,
        room_id: UUID,
        commit: RecoveryCommit,
    ) -> None:
        try:
            previous_runtime = self._store.load_room_runtime(room_id)
        except PersistenceNotFoundError:
            previous_runtime = None
        if commit.events:
            self._store.save_core(room_id, commit.next_state, commit.events)
            if commit.command_result.accepted:
                key = CommandDedupeKey(
                    command_id=commit.command_result.command_id,
                    actor_type="host",
                    actor_key="host",
                )
                self._store.save_command_results(
                    room_id,
                    {key: commit.command_result},
                )
        if commit.events and (previous_runtime is None or commit.runtime != previous_runtime):
            self._store.save_room_runtime(room_id, commit.runtime)
        if commit.snapshot is not None:
            self._store.save_snapshot(commit.snapshot)
        if commit.audit is not None:
            existing_audit = self._store.load_recovery_audit(room_id)
            if not any(record.record_id == commit.audit.record_id for record in existing_audit):
                self._store.append_recovery_audit(commit.audit)
        previous_tombstones = (
            set(previous_runtime.discarded_command_tombstones)
            if previous_runtime is not None
            else set()
        )
        for key in commit.runtime.discarded_command_tombstones:
            if key in previous_tombstones:
                continue
            connection.execute(
                """
                DELETE FROM command_results
                WHERE room_id = ? AND command_id = ? AND actor_type = ? AND actor_key = ?
                """,
                (
                    str(room_id),
                    str(key.command_id),
                    key.actor_type,
                    key.actor_key,
                ),
            )

    def correct(
        self,
        room_id: UUID,
        command: HostPatchCommand,
        actor: AuthenticatedActor,
        now: datetime,
    ) -> CommandResult:
        core, runtime = self._load_core_and_runtime(room_id, now)
        command_id = deterministic_uuid(
            NAMESPACE_URL,
            room_id,
            core.state.revision,
            "HOST_PATCH",
            command.patch.patch_type,
        )
        commit = self.stage_patch(
            room_id=room_id,
            core=core,
            runtime=runtime,
            command=command,
            actor=actor,
            now=now,
            command_id=command_id,
            expected_revision=core.state.revision,
        )
        with self._store.transaction() as connection:
            self.persist_commit(connection, room_id, commit)
        return commit.command_result

    def rewind(
        self,
        room_id: UUID,
        command: HostRewindToSnapshotCommand,
        actor: AuthenticatedActor,
        now: datetime,
    ) -> CommandResult:
        core, runtime = self._load_core_and_runtime(room_id, now)
        command_id = deterministic_uuid(
            NAMESPACE_URL,
            room_id,
            core.state.revision,
            "HOST_REWIND_TO_SNAPSHOT",
            command.snapshot_id,
        )
        commit = self.stage_rewind(
            core=core,
            runtime=runtime,
            command=command,
            actor=actor,
            now=now,
            command_id=command_id,
            expected_revision=core.state.revision,
        )
        with self._store.transaction() as connection:
            self.persist_commit(connection, room_id, commit)
        return commit.command_result

    def force_template(
        self,
        room_id: UUID,
        command: HostForceTemplateCommand,
        actor: AuthenticatedActor,
        now: datetime,
    ) -> CommandResult:
        core, runtime = self._load_core_and_runtime(room_id, now)
        command_id = deterministic_uuid(
            NAMESPACE_URL,
            room_id,
            core.state.revision,
            "HOST_FORCE_TEMPLATE",
        )
        commit = self.stage_force_template(
            core=core,
            runtime=runtime,
            command=command,
            actor=actor,
            now=now,
            command_id=command_id,
            expected_revision=core.state.revision,
        )
        with self._store.transaction() as connection:
            self.persist_commit(connection, room_id, commit)
        return commit.command_result

    def _load_core_and_runtime(
        self,
        room_id: UUID,
        now: datetime,
    ) -> tuple[GameCore, PersistedRoomRuntime]:
        state, events = self._store.load_core(room_id)
        core = GameCore.restore(
            room_id=room_id,
            seed=state.seed,
            clock=FrozenClock(now),
            state=state,
            events=events,
            command_results=self._store.load_command_results(room_id),
        )
        return core, self._store.load_room_runtime(room_id)

    @staticmethod
    def _envelope(
        *,
        room_id: UUID,
        command: HostPatchCommand,
        command_id: UUID,
        expected_revision: int,
        now: datetime,
    ) -> CommandEnvelope:
        return CommandEnvelope(
            command_id=command_id,
            room_id=room_id,
            expected_revision=expected_revision,
            issued_at=now,
            payload=command,
        )

    @staticmethod
    def _snapshot_for_state(
        state: GameState,
        reason: SnapshotReason,
        *,
        snapshot_id: UUID,
        created_at: datetime,
    ) -> PersistedSnapshot:
        return PersistedSnapshot(
            snapshot_id=snapshot_id,
            room_id=state.room_id,
            revision=state.revision,
            reason=reason,
            state=state,
            event_count=state.event_count,
            created_at=created_at,
        )

    @staticmethod
    def _snapshot_id_for_command(
        command_id: UUID,
        reason: SnapshotReason,
    ) -> UUID:
        return deterministic_uuid(
            NAMESPACE_URL,
            command_id,
            reason.value,
        )

    @staticmethod
    def _audit_id(
        room_id: UUID,
        command_id: UUID,
        patch_type: str,
        status: str,
    ) -> UUID:
        return deterministic_uuid(
            NAMESPACE_URL,
            room_id,
            command_id,
            patch_type,
            status,
        )

    @staticmethod
    def _replace_runtime(
        runtime: PersistedRoomRuntime,
        *,
        next_domain_seq: int,
    ) -> PersistedRoomRuntime:
        return PersistedRoomRuntime(
            outbox_seq=runtime.outbox_seq,
            domain_to_transport=dict(runtime.domain_to_transport),
            completed_domain_seqs=tuple(runtime.completed_domain_seqs),
            processed_announcement_seq=runtime.processed_announcement_seq,
            published_message_ids=tuple(runtime.published_message_ids),
            next_domain_seq=next_domain_seq,
            recovery_epoch=runtime.recovery_epoch,
            discarded_command_tombstones=tuple(runtime.discarded_command_tombstones),
        )

    @staticmethod
    def _correction_details(
        events: tuple[DomainEvent, ...],
    ) -> tuple[dict[str, JsonValue], str]:
        for event in events:
            if event.event_type is not EventType.HOST_CORRECTION_APPLIED:
                continue
            payload = EVENT_PAYLOAD_MODELS[event.event_type].validate_json_payload(
                event.fact_payload
            )
            if isinstance(payload, HostCorrectionAudit):
                serialized = payload.model_dump(mode="json")
                return cast(dict[str, JsonValue], serialized["diff"]), payload.reason
        return {}, "host recovery patch"

    @staticmethod
    def _discarded_commands(
        core: GameCore,
        restored_revision: int,
    ) -> tuple[CommandDedupeKey, ...]:
        discarded: list[CommandDedupeKey] = []
        for legacy_key, result in core.command_dedupe_cache.items():
            command_id, actor_type, actor_key = legacy_key
            if result.revision <= restored_revision:
                continue
            if actor_type == "host":
                key = CommandDedupeKey(
                    command_id=command_id,
                    actor_type="host",
                    actor_key="host",
                )
            else:
                key = CommandDedupeKey(
                    command_id=command_id,
                    actor_type="seat",
                    actor_key=str(actor_key),
                )
            discarded.append(key)
        return tuple(discarded)

    @staticmethod
    def _can_recover(
        state: GameState,
        actor: AuthenticatedActor,
        expected_revision: int,
    ) -> bool:
        return (
            HostRecoveryService._is_authorized_host(state, actor)
            and state.phase is not Phase.GAME_END
            and state.paused
            and expected_revision == state.revision
        )

    @staticmethod
    def _is_authorized_host(
        state: GameState,
        actor: AuthenticatedActor,
    ) -> bool:
        return (
            actor.actor_type == "host" and actor.seat_id is None and actor.room_id == state.room_id
        )

    @staticmethod
    def _recovery_error(
        state: GameState,
        actor: AuthenticatedActor,
        expected_revision: int,
    ) -> CommandErrorCode:
        if (
            actor.actor_type != "host"
            or actor.seat_id is not None
            or actor.room_id != state.room_id
        ):
            return CommandErrorCode.ACTOR_NOT_AUTHORIZED
        if state.phase is Phase.GAME_END:
            return CommandErrorCode.GAME_ENDED
        if not state.paused:
            return CommandErrorCode.ILLEGAL_PHASE
        if expected_revision != state.revision:
            return CommandErrorCode.REVISION_CONFLICT
        return CommandErrorCode.INVALID_TARGET

    @classmethod
    def _rejected_commit(
        cls,
        *,
        state: GameState,
        runtime: PersistedRoomRuntime,
        command_id: UUID,
        patch_type: str,
        error_code: CommandErrorCode,
        now: datetime,
    ) -> RecoveryCommit:
        result = CommandResult(
            command_id=command_id,
            accepted=False,
            revision=state.revision,
            event_ids=(),
            error_code=error_code,
        )
        audit = PersistedRecoveryAudit(
            record_id=cls._audit_id(
                state.room_id,
                command_id,
                patch_type,
                "REJECTED",
            ),
            room_id=state.room_id,
            command_id=command_id,
            status="REJECTED",
            patch_type=patch_type,
            before_revision=state.revision,
            after_revision=state.revision,
            before_state=state,
            after_state=None,
            diff={},
            reason=error_code.value,
            created_at=now,
        )
        return RecoveryCommit(
            next_state=state,
            events=(),
            command_result=result,
            runtime=runtime,
            snapshot=None,
            audit=audit,
        )
