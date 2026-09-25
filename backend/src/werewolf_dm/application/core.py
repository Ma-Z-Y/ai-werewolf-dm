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
from werewolf_dm.domain.state_machine import (
    apply_command,
    deterministic_uuid,
    initial_state,
)
from werewolf_dm.domain.visibility import SeatView, project_seat_view


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

    def submit(
        self,
        envelope: CommandEnvelope,
        actor: AuthenticatedActor,
    ) -> CommandResult:
        envelope = CommandEnvelope.revalidate(envelope)
        actor = AuthenticatedActor.revalidate(actor)
        if envelope.room_id != self._state.room_id or actor.room_id != self._state.room_id:
            return CommandResult(
                command_id=envelope.command_id,
                accepted=False,
                revision=self._state.revision,
                event_ids=(),
                error_code=CommandErrorCode.ACTOR_NOT_AUTHORIZED,
            )
        cache_key = (envelope.command_id, actor.actor_type, actor.seat_id)
        cached = self.command_dedupe_cache.get(cache_key)
        if cached is not None:
            return cached
        if envelope.expected_revision != self._state.revision:
            return CommandResult(
                command_id=envelope.command_id,
                accepted=False,
                revision=self._state.revision,
                event_ids=(),
                error_code=CommandErrorCode.REVISION_CONFLICT,
            )

        outcome = apply_command(self._state, envelope, actor, self.clock())
        if outcome.error_code is not None:
            result = CommandResult(
                command_id=envelope.command_id,
                accepted=False,
                revision=self._state.revision,
                event_ids=(),
                error_code=outcome.error_code,
            )
            self.command_dedupe_cache[cache_key] = result
            return result

        self._state = outcome.next_state
        self._events.extend(outcome.events)
        result = CommandResult(
            command_id=envelope.command_id,
            accepted=True,
            revision=self._state.revision,
            event_ids=tuple(event.event_id for event in outcome.events),
        )
        self.command_dedupe_cache[cache_key] = result
        return result

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
