from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID

from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    CommandEnvelope,
    CommandErrorCode,
    CommandResult,
    DomainEvent,
    SystemTimeout,
)
from werewolf_dm.domain.model import GameState
from werewolf_dm.domain.replay import event_log_digest
from werewolf_dm.domain.state_machine import (
    apply_command,
    deterministic_uuid,
    initial_state,
    validate_invariants,
)
from werewolf_dm.domain.visibility import SeatView, project_seat_view
from werewolf_dm.infrastructure.persistence import (
    CommandDedupeKey,
    PersistedAdmission,
    PersistedDMTrace,
    PersistedPublication,
    PersistedRoomRuntime,
    PersistedSnapshot,
    PersistenceConflictError,
    SQLiteRoomStore,
)


class Clock(Protocol):
    def __call__(self) -> datetime: ...

    def set(self, now: datetime) -> None: ...


class FrozenClock:
    def __init__(self, now: datetime):
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


@dataclass(frozen=True, slots=True)
class DomainSeqReservation:
    start: int
    count: int

    def __post_init__(self) -> None:
        if self.start < 1 or self.count < 1:
            raise ValueError("domain sequence reservation must be positive")


class DomainSeqAllocator:
    def __init__(self, start: int) -> None:
        if start < 1:
            raise ValueError("domain sequence watermark must be positive")
        self._watermark = start
        self._open_reservations: set[DomainSeqReservation] = set()

    def current(self) -> int:
        return self._watermark

    def reserve(self, count: int = 1) -> DomainSeqReservation:
        reservation = DomainSeqReservation(start=self._watermark, count=count)
        self._watermark += count
        self._open_reservations.add(reservation)
        return reservation

    def commit(self, reservation: DomainSeqReservation) -> None:
        if reservation not in self._open_reservations:
            raise ValueError("domain sequence reservation is not open")
        self._open_reservations.remove(reservation)

    def rollback(self, reservation: DomainSeqReservation) -> None:
        if reservation not in self._open_reservations:
            raise ValueError("domain sequence reservation is not open")
        if reservation.start + reservation.count != self._watermark:
            raise ValueError("domain sequence reservations must roll back in reverse order")
        self._watermark = reservation.start
        self._open_reservations.remove(reservation)


@dataclass(frozen=True, slots=True)
class CoreMutation:
    next_state: GameState
    appended_events: tuple[DomainEvent, ...]
    command_result: CommandResult
    dedupe_key: CommandDedupeKey | None
    cache_result: bool
    seq_reservations: tuple[DomainSeqReservation, ...] = ()


class PersistenceCoordinator:
    def __init__(self, store: SQLiteRoomStore | None) -> None:
        self.store = store

    def commit_core_mutation(
        self,
        room_id: UUID,
        mutation: CoreMutation,
        runtime: PersistedRoomRuntime,
        traces: tuple[PersistedDMTrace, ...],
        snapshot: PersistedSnapshot | None = None,
    ) -> None:
        if self.store is None:
            return
        with self.store.transaction():
            self.store.save_core(
                room_id,
                mutation.next_state,
                mutation.appended_events,
            )
            if mutation.cache_result and mutation.dedupe_key is not None:
                self.store.save_command_results(
                    room_id,
                    {mutation.dedupe_key: mutation.command_result},
                )
            self._save_traces(room_id, traces)
            self.store.save_room_runtime(room_id, runtime)
            if snapshot is not None:
                self.store.save_snapshot(snapshot)

    def commit_runtime_update(
        self,
        room_id: UUID,
        runtime: PersistedRoomRuntime,
        traces: tuple[PersistedDMTrace, ...],
        admissions: tuple[PersistedAdmission, ...] = (),
        publications: tuple[PersistedPublication, ...] = (),
    ) -> None:
        self._validate_runtime_records(runtime, admissions, publications)
        if self.store is None:
            return
        with self.store.transaction():
            self._save_traces(room_id, traces)
            self.store.save_room_runtime(room_id, runtime)

    def _save_traces(
        self,
        room_id: UUID,
        traces: tuple[PersistedDMTrace, ...],
    ) -> None:
        if self.store is None:
            return
        for persisted_trace in traces:
            self.store.save_dm_trace(
                room_id,
                persisted_trace.trace_kind,
                persisted_trace.trace,
            )

    @staticmethod
    def _validate_runtime_records(
        runtime: PersistedRoomRuntime,
        admissions: tuple[PersistedAdmission, ...],
        publications: tuple[PersistedPublication, ...],
    ) -> None:
        published_ids = set(runtime.published_message_ids)
        for admission in admissions:
            if admission.recovery_epoch != runtime.recovery_epoch:
                raise PersistenceConflictError("admission recovery epoch is stale")
            if admission.transport_seq > runtime.outbox_seq:
                raise PersistenceConflictError("admission transport sequence is ahead of runtime")
            if runtime.domain_to_transport.get(admission.domain_seq) != admission.transport_seq:
                raise PersistenceConflictError("admission is missing from runtime mapping")
            if admission.message_id not in published_ids:
                raise PersistenceConflictError("admission message is missing from runtime")
        for publication in publications:
            if publication.recovery_epoch != runtime.recovery_epoch:
                raise PersistenceConflictError("publication recovery epoch is stale")
            if publication.transport_seq > runtime.outbox_seq:
                raise PersistenceConflictError("publication transport sequence is ahead of runtime")
            if publication.message_id not in published_ids:
                raise PersistenceConflictError("publication message is missing from runtime")


class GameCore:
    def __init__(self, room_id: UUID, seed: int, clock: Clock):
        if type(room_id) is not UUID:
            raise ValueError("room_id must be a standard UUID instance")
        self._state = initial_state(room_id, seed)
        self.seed = seed
        self.clock = clock
        self._events: list[DomainEvent] = []
        self.command_dedupe_cache: dict[tuple[UUID, str, int | None], CommandResult] = {}

    @property
    def state(self) -> "GameState":
        return self._state

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._events)

    @classmethod
    def new_room(cls, room_id: UUID, seed: int, clock: Clock) -> "GameCore":
        return cls(room_id=room_id, seed=seed, clock=clock)

    @classmethod
    def restore(
        cls,
        *,
        room_id: UUID,
        seed: int,
        clock: Clock,
        state: GameState,
        events: tuple[DomainEvent, ...],
        command_results: Mapping[CommandDedupeKey, CommandResult] | None = None,
    ) -> "GameCore":
        if type(room_id) is not UUID:
            raise ValueError("room_id must be a standard UUID instance")
        state = GameState.revalidate(state)
        restored_events = tuple(DomainEvent.revalidate(event) for event in events)
        if state.room_id != room_id:
            raise ValueError("restored state room does not match room_id")
        if state.seed != seed:
            raise ValueError("restored state seed does not match seed")
        if any(event.room_id != room_id for event in restored_events):
            raise ValueError("restored event belongs to another room")
        if len({event.event_id for event in restored_events}) != len(restored_events):
            raise ValueError("restored event ids must be unique")
        revisions = tuple(event.revision for event in restored_events)
        if revisions != tuple(sorted(revisions)):
            raise ValueError("restored events must be ordered by revision")
        previous_revision: int | None = None
        for revision in revisions:
            if previous_revision is None:
                previous_revision = revision
                continue
            if revision == previous_revision:
                continue
            if revision != previous_revision + 1:
                raise ValueError("restored event revisions must be continuous")
            previous_revision = revision
        if state.event_count != len(restored_events):
            raise ValueError("restored state event_count does not match history")
        if state.event_log_digest != event_log_digest(restored_events):
            raise ValueError("restored state event_log_digest does not match history")
        if restored_events and state.revision != restored_events[-1].revision:
            raise ValueError("restored state revision does not match history")
        if not restored_events and state.revision != 0:
            raise ValueError("empty restored event history requires revision zero")
        validate_invariants(state)

        core = cls(room_id=room_id, seed=seed, clock=clock)
        core._state = state
        core._events = list(restored_events)
        core.command_dedupe_cache = {}
        for key, result in (command_results or {}).items():
            result = CommandResult.revalidate(result)
            if key.command_id != result.command_id:
                raise ValueError("command result does not match dedupe key")
            core.command_dedupe_cache[core._legacy_dedupe_key(key)] = result
        return core

    def stage_submit(
        self,
        envelope: CommandEnvelope,
        actor: AuthenticatedActor,
        *,
        next_domain_seq: DomainSeqAllocator,
    ) -> CoreMutation:
        envelope = CommandEnvelope.revalidate(envelope)
        actor = AuthenticatedActor.revalidate(actor)
        if (
            envelope.room_id != self._state.room_id
            or actor.room_id != self._state.room_id
            or actor.actor_type == "display"
        ):
            return self._mutation(
                next_state=self._state,
                events=(),
                result=CommandResult(
                    command_id=envelope.command_id,
                    accepted=False,
                    revision=self._state.revision,
                    event_ids=(),
                    error_code=CommandErrorCode.ACTOR_NOT_AUTHORIZED,
                ),
                dedupe_key=None,
                cache_result=False,
            )
        dedupe_key = self._dedupe_key(envelope.command_id, actor)
        cache_key = self._legacy_dedupe_key(dedupe_key)
        cached = self.command_dedupe_cache.get(cache_key)
        if cached is not None:
            return self._mutation(
                next_state=self._state,
                events=(),
                result=cached,
                dedupe_key=dedupe_key,
                cache_result=False,
            )
        if envelope.expected_revision != self._state.revision:
            return self._mutation(
                next_state=self._state,
                events=(),
                result=CommandResult(
                    command_id=envelope.command_id,
                    accepted=False,
                    revision=self._state.revision,
                    event_ids=(),
                    error_code=CommandErrorCode.REVISION_CONFLICT,
                ),
                dedupe_key=dedupe_key,
                cache_result=False,
            )

        outcome = apply_command(self._state, envelope, actor, self.clock())
        reservations = self._reserve_outbox_delta(
            self._state,
            outcome.next_state,
            next_domain_seq,
        )
        return self._mutation(
            next_state=outcome.next_state,
            events=outcome.events,
            result=CommandResult(
                command_id=envelope.command_id,
                accepted=outcome.error_code is None,
                revision=outcome.next_state.revision,
                event_ids=tuple(event.event_id for event in outcome.events),
                error_code=outcome.error_code,
            ),
            dedupe_key=dedupe_key,
            cache_result=True,
            seq_reservations=reservations,
        )

    def stage_tick(self, *, next_domain_seq: DomainSeqAllocator) -> CoreMutation:
        state = self._state
        appended_events: list[DomainEvent] = []
        results: list[CommandResult] = []
        reservations: list[DomainSeqReservation] = []
        while (
            not state.paused and state.deadline_at is not None and self.clock() >= state.deadline_at
        ):
            timeout = SystemTimeout(
                timeout_id=deterministic_uuid(
                    NAMESPACE_URL,
                    state.room_id,
                    state.revision,
                    state.phase,
                ),
                room_id=state.room_id,
                expected_revision=state.revision,
                phase=state.phase,
                occurred_at=state.deadline_at,
            )
            outcome = apply_command(state, timeout, None, state.deadline_at)
            result = CommandResult(
                command_id=timeout.timeout_id,
                accepted=outcome.error_code is None,
                revision=outcome.next_state.revision,
                event_ids=tuple(event.event_id for event in outcome.events),
                error_code=outcome.error_code,
            )
            results.append(result)
            if outcome.error_code is not None:
                break
            reservations.extend(
                self._reserve_outbox_delta(
                    state,
                    outcome.next_state,
                    next_domain_seq,
                )
            )
            appended_events.extend(outcome.events)
            state = outcome.next_state

        if not results:
            no_op_id = deterministic_uuid(
                NAMESPACE_URL,
                self._state.room_id,
                self._state.revision,
                "noop-tick",
            )
            result = CommandResult(
                command_id=no_op_id,
                accepted=False,
                revision=self._state.revision,
                event_ids=(),
                error_code=None,
            )
        else:
            accepted = any(candidate.accepted for candidate in results)
            result = CommandResult(
                command_id=results[-1].command_id,
                accepted=accepted,
                revision=state.revision,
                event_ids=tuple(
                    event_id for candidate in results for event_id in candidate.event_ids
                ),
                error_code=None if accepted else results[-1].error_code,
            )
        return self._mutation(
            next_state=state,
            events=tuple(appended_events),
            result=result,
            dedupe_key=None,
            cache_result=False,
            seq_reservations=tuple(reservations),
        )

    def submit(
        self,
        envelope: CommandEnvelope,
        actor: AuthenticatedActor,
    ) -> CommandResult:
        allocator = DomainSeqAllocator(self._next_domain_seq(self._state))
        mutation = self.stage_submit(
            envelope,
            actor,
            next_domain_seq=allocator,
        )
        for reservation in mutation.seq_reservations:
            allocator.commit(reservation)
        self.commit(mutation)
        return mutation.command_result

    def commit(self, mutation: CoreMutation) -> None:
        self._state = mutation.next_state
        if mutation.appended_events:
            self._events.extend(mutation.appended_events)
        if mutation.cache_result and mutation.dedupe_key is not None:
            self.command_dedupe_cache[self._legacy_dedupe_key(mutation.dedupe_key)] = (
                mutation.command_result
            )

    def reconnect(self, actor: AuthenticatedActor) -> SeatView:
        actor = AuthenticatedActor.revalidate(actor)
        if actor.actor_type != "seat" or actor.seat_id is None:
            raise ValueError("reconnect requires a seat actor")
        return project_seat_view(self.state, actor.seat_id, actor)

    def apply_timeout(self, timeout: SystemTimeout, now: datetime) -> CommandResult:
        timeout = SystemTimeout.revalidate(timeout)
        outcome = apply_command(self._state, timeout, None, now)
        if outcome.error_code is None:
            self._state = outcome.next_state
            self._events.extend(outcome.events)
        return CommandResult(
            command_id=timeout.timeout_id,
            accepted=outcome.error_code is None,
            revision=self._state.revision,
            event_ids=tuple(event.event_id for event in outcome.events),
            error_code=outcome.error_code,
        )

    def tick(self) -> tuple[CommandResult, ...]:
        results: list[CommandResult] = []
        while (
            not self._state.paused
            and self._state.deadline_at is not None
            and self.clock() >= self._state.deadline_at
        ):
            timeout = SystemTimeout(
                timeout_id=deterministic_uuid(
                    NAMESPACE_URL,
                    self._state.room_id,
                    self._state.revision,
                    self._state.phase,
                ),
                room_id=self._state.room_id,
                expected_revision=self._state.revision,
                phase=self._state.phase,
                occurred_at=self._state.deadline_at,
            )
            result = self.apply_timeout(timeout, self._state.deadline_at)
            results.append(result)
            if not result.accepted:
                break
        return tuple(results)

    @staticmethod
    def _dedupe_key(
        command_id: UUID,
        actor: AuthenticatedActor,
    ) -> CommandDedupeKey:
        if actor.actor_type == "seat":
            assert actor.seat_id is not None
            return CommandDedupeKey(
                command_id=command_id,
                actor_type="seat",
                actor_key=str(actor.seat_id),
            )
        if actor.actor_type == "host":
            return CommandDedupeKey(
                command_id=command_id,
                actor_type="host",
                actor_key="host",
            )
        raise ValueError("display actors cannot submit commands")

    @staticmethod
    def _legacy_dedupe_key(
        key: CommandDedupeKey,
    ) -> tuple[UUID, str, int | None]:
        if key.actor_type == "host":
            return key.command_id, "host", None
        return key.command_id, "seat", int(key.actor_key)

    @staticmethod
    def _next_domain_seq(state: GameState) -> int:
        if not state.outbox:
            return 1
        return state.outbox[-1].seq + 1

    @classmethod
    def _reserve_outbox_delta(
        cls,
        previous_state: GameState,
        next_state: GameState,
        next_domain_seq: DomainSeqAllocator,
    ) -> tuple[DomainSeqReservation, ...]:
        previous_max = previous_state.outbox[-1].seq if previous_state.outbox else 0
        next_max = next_state.outbox[-1].seq if next_state.outbox else 0
        count = max(0, next_max - previous_max)
        if count == 0:
            return ()
        return (next_domain_seq.reserve(count),)

    @staticmethod
    def _mutation(
        *,
        next_state: GameState,
        events: tuple[DomainEvent, ...],
        result: CommandResult,
        dedupe_key: CommandDedupeKey | None,
        cache_result: bool,
        seq_reservations: tuple[DomainSeqReservation, ...] = (),
    ) -> CoreMutation:
        return CoreMutation(
            next_state=next_state,
            appended_events=events,
            command_result=result,
            dedupe_key=dedupe_key,
            cache_result=cache_result,
            seq_reservations=seq_reservations,
        )
