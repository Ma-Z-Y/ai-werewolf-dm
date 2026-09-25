from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import model_validator

from werewolf_dm.application.core import FrozenClock, GameCore
from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    CommandEnvelope,
    CommandResult,
    DomainEvent,
    SystemTimeout,
)
from werewolf_dm.domain.model import GameState, StrictModel
from werewolf_dm.domain.replay import state_hash


class ReplayStep(StrictModel):
    actor: AuthenticatedActor | None = None
    envelope: CommandEnvelope | None = None
    timeout: SystemTimeout | None = None
    now: datetime

    @model_validator(mode="after")
    def check_exactly_one_trigger(self) -> Self:
        if self.envelope is not None:
            if self.timeout is not None:
                raise ValueError("command step cannot contain timeout")
            if self.actor is None:
                raise ValueError("command step requires actor")
            return self
        if self.timeout is None:
            raise ValueError("exactly one of envelope or timeout must be set")
        if self.actor is not None:
            raise ValueError("timeout step cannot contain actor")
        return self


class ReplayResult(StrictModel):
    final_state: GameState
    command_results: tuple[CommandResult, ...]
    timeout_results: tuple[CommandResult, ...]
    events: tuple[DomainEvent, ...]
    state_hashes: tuple[str, ...]


def replay(room_id: UUID, seed: int, steps: tuple[ReplayStep, ...]) -> ReplayResult:
    if not steps:
        raise ValueError("replay requires at least one step")
    validated_steps = tuple(ReplayStep.revalidate(step) for step in steps)
    core = GameCore.new_room(room_id, seed, FrozenClock(validated_steps[0].now))
    hashes = [state_hash(core.state)]
    command_results: list[CommandResult] = []
    timeout_results: list[CommandResult] = []
    for step in validated_steps:
        core.clock.set(step.now)
        if step.envelope is not None:
            if step.actor is None:
                raise ValueError("command step requires actor")
            command_results.append(core.submit(step.envelope, step.actor))
        else:
            if step.timeout is None:
                raise ValueError("replay step requires envelope or timeout")
            timeout_results.append(core.apply_timeout(step.timeout, step.now))
        hashes.append(state_hash(core.state))
    return ReplayResult(
        final_state=core.state,
        command_results=tuple(command_results),
        timeout_results=tuple(timeout_results),
        events=tuple(core.events),
        state_hashes=tuple(hashes),
    )
