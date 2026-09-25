from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.factories import (
    ROOM_ID,
    minimal_night_script,
    role_reveal_script,
)
from werewolf_dm.application.replay import ReplayStep, replay
from werewolf_dm.domain.contracts import CommandErrorCode, SystemTimeout
from werewolf_dm.domain.enums import EventType, Phase
from werewolf_dm.domain.replay import state_hash
from werewolf_dm.domain.state_machine import initial_state


def test_same_seed_and_commands_produce_same_hashes():
    steps = minimal_night_script()
    first = replay(ROOM_ID, seed=101, steps=steps)
    second = replay(ROOM_ID, seed=101, steps=steps)

    assert first.final_state == second.final_state
    assert first.command_results == second.command_results
    assert first.state_hashes == second.state_hashes


def test_different_seed_changes_role_assignment_hash():
    steps = role_reveal_script()
    first = replay(ROOM_ID, seed=101, steps=steps)
    second = replay(ROOM_ID, seed=202, steps=steps)

    assert first.state_hashes[-1] != second.state_hashes[-1]


def test_state_hash_uses_only_frozen_hash_fields():
    state = initial_state(ROOM_ID, seed=101)
    changed_seed = state.model_copy(update={"seed": 202})

    assert state_hash(state) == state_hash(changed_seed)
    assert len(state_hash(state)) == 64


def test_command_and_event_logs_replay_identically():
    steps = minimal_night_script(seed=102)
    first = replay(ROOM_ID, seed=102, steps=steps)
    second = replay(ROOM_ID, seed=102, steps=steps)

    assert all(result.accepted for result in first.command_results)
    assert first.final_state.phase is Phase.DAY_DISCUSSION
    assert [event.event_type for event in first.events] == [
        event.event_type for event in second.events
    ]
    assert [event.causation_id for event in first.events] == [
        event.causation_id for event in second.events
    ]
    assert first.events == second.events
    assert first.final_state.private_facts == second.final_state.private_facts


def test_expected_revision_conflict_is_replayed_as_rejection():
    step = minimal_night_script()[0]
    stale_step = step.model_copy(
        update={
            "envelope": step.envelope.model_copy(update={"expected_revision": 99}),
        }
    )

    result = replay(ROOM_ID, seed=101, steps=(stale_step,))

    assert result.command_results[0].accepted is False
    assert result.command_results[0].error_code is CommandErrorCode.REVISION_CONFLICT
    assert result.command_results[0].revision == 0
    assert result.state_hashes[0] == result.state_hashes[1]


def test_timeout_replay_uses_apply_command():
    command_steps = minimal_night_script()[:18]
    deadline = command_steps[-1].now + timedelta(seconds=60)
    timeout_step = ReplayStep(
        timeout=SystemTimeout(
            timeout_id=command_steps[-1].envelope.command_id,
            room_id=ROOM_ID,
            expected_revision=18,
            phase=Phase.NIGHT_WOLF,
            occurred_at=deadline,
        ),
        now=deadline,
    )

    result = replay(ROOM_ID, seed=101, steps=(*command_steps, timeout_step))

    assert result.final_state.phase is Phase.NIGHT_SEER
    assert result.final_state.wolf_decision.target_seat_id is None
    assert result.final_state.wolf_decision.locked is True
    assert len(result.command_results) == len(command_steps)
    assert len(result.state_hashes) == len(command_steps) + 2
    assert any(event.event_type is EventType.TIMEOUT_APPLIED for event in result.events)


def test_rejected_timeout_is_visible_and_does_not_change_hash():
    step = minimal_night_script()[0]
    timeout_step = ReplayStep(
        timeout=SystemTimeout(
            timeout_id=uuid4(),
            room_id=ROOM_ID,
            expected_revision=0,
            phase=Phase.NIGHT_WOLF,
            occurred_at=step.now,
        ),
        now=step.now,
    )

    result = replay(ROOM_ID, seed=101, steps=(timeout_step,))

    assert len(result.timeout_results) == 1
    assert result.timeout_results[0].accepted is False
    assert result.timeout_results[0].error_code is CommandErrorCode.ILLEGAL_PHASE
    assert result.state_hashes[0] == result.state_hashes[1]


def test_replay_step_rejects_command_without_actor():
    step = minimal_night_script()[0]

    with pytest.raises(ValidationError):
        ReplayStep(envelope=step.envelope, now=step.now)


def test_replay_step_rejects_timeout_with_actor():
    step = minimal_night_script()[0]

    with pytest.raises(ValidationError):
        ReplayStep(
            actor=step.actor,
            timeout=SystemTimeout(
                timeout_id=uuid4(),
                room_id=ROOM_ID,
                expected_revision=0,
                phase=Phase.ROLE_REVEAL,
                occurred_at=step.now,
            ),
            now=step.now,
        )


def test_replay_step_model_copy_revalidates_combination():
    step = minimal_night_script()[0]

    with pytest.raises(ValidationError):
        step.model_copy(
            update={
                "timeout": SystemTimeout(
                    timeout_id=uuid4(),
                    room_id=ROOM_ID,
                    expected_revision=0,
                    phase=Phase.ROLE_REVEAL,
                    occurred_at=step.now,
                )
            }
        )
    with pytest.raises(ValidationError):
        step.model_copy(update={"actor": None})


def test_replay_revalidates_model_constructed_steps():
    step = role_reveal_script()[0]
    timeout = SystemTimeout(
        timeout_id=uuid4(),
        room_id=ROOM_ID,
        expected_revision=0,
        phase=Phase.ROLE_REVEAL,
        occurred_at=step.now,
    )
    illegal_step = ReplayStep.model_construct(
        actor=step.actor,
        envelope=step.envelope,
        timeout=timeout,
        now=step.now,
    )

    with pytest.raises(ValidationError):
        replay(ROOM_ID, seed=101, steps=(illegal_step,))


def test_replay_rejects_empty_step_list():
    with pytest.raises(ValueError):
        replay(ROOM_ID, seed=101, steps=())
