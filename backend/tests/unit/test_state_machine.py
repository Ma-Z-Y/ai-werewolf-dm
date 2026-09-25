from collections import Counter
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid4

import pytest

from tests.factories import tick_to_role_reveal_deadline
from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    CommandEnvelope,
    CommandErrorCode,
    ConfirmRoleCommand,
    HostPauseCommand,
    HostResumeCommand,
    JoinRoomCommand,
    SetReadyCommand,
    SystemTimeout,
    VoteCommand,
)
from werewolf_dm.domain.enums import Phase
from werewolf_dm.domain.model import ROLE_COUNTS
from werewolf_dm.domain.state_machine import apply_command, deterministic_uuid


def test_st_001_six_ready_players_enter_role_reveal(core, actor, envelope, submit):
    for seat_id in range(1, 7):
        seat = actor(seat_id)
        submit(
            core,
            seat,
            envelope(
                seat,
                JoinRoomCommand(seat_id=seat_id, display_name=f"P{seat_id}"),
                expected_revision=core.state.revision,
            ),
        )
        submit(
            core,
            seat,
            envelope(
                seat,
                SetReadyCommand(ready=True),
                expected_revision=core.state.revision,
            ),
        )
    assert core.state.phase is Phase.ROLE_REVEAL
    assert dict(Counter(player.role for player in core.state.players)) == dict(ROLE_COUNTS)


def test_apply_command_revalidates_constructed_inputs(core, actor):
    envelope = CommandEnvelope.model_construct(
        schema_version="command.v1",
        command_id=uuid4(),
        room_id=core.state.room_id,
        expected_revision=0.0,
        issued_at=core.clock(),
        payload=JoinRoomCommand(seat_id=1, display_name="A"),
    )

    with pytest.raises(ValueError):
        apply_command(core.state, envelope, actor(1), core.clock())

    forged_actor = AuthenticatedActor.model_construct(
        actor_type="seat",
        seat_id=True,
        room_id=core.state.room_id,
    )
    valid_envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=core.state.room_id,
        expected_revision=core.state.revision,
        issued_at=core.clock(),
        payload=JoinRoomCommand(seat_id=1, display_name="A"),
    )

    with pytest.raises(ValueError):
        apply_command(core.state, valid_envelope, forged_actor, core.clock())

    forged_timeout = SystemTimeout.model_construct(
        schema_version="system-timeout.v1",
        timeout_id=uuid4(),
        room_id=core.state.room_id,
        expected_revision=0.0,
        phase=Phase.ROLE_REVEAL,
        occurred_at=core.clock(),
    )

    with pytest.raises(ValueError):
        apply_command(core.state, forged_timeout, None, core.clock())

    assert core.state.revision == 0
    assert core.state.players == ()


def test_st_002_all_role_confirmations_enter_first_night(
    core_at_role_reveal,
    actor,
    envelope,
    submit,
):
    core = core_at_role_reveal
    for seat_id in range(1, 7):
        seat = actor(seat_id)
        submit(
            core,
            seat,
            envelope(
                seat,
                ConfirmRoleCommand(),
                expected_revision=core.state.revision,
            ),
        )
    assert core.state.phase is Phase.NIGHT_WOLF
    assert core.state.day == 1


def test_st_013_illegal_phase_rejects_without_revision_change(core, actor, envelope, submit):
    revision = core.state.revision
    seat = actor(1)
    result = submit(
        core,
        seat,
        envelope(
            seat,
            VoteCommand(target_seat_id=2),
            expected_revision=revision,
        ),
    )
    assert result.accepted is False
    assert result.error_code.value == "ILLEGAL_PHASE"
    assert core.state.revision == revision


def test_st_014_successful_command_increases_revision_once(core, actor, envelope, submit):
    revision = core.state.revision
    seat = actor(1)
    result = submit(
        core,
        seat,
        envelope(
            seat,
            JoinRoomCommand(seat_id=1, display_name="A"),
            expected_revision=revision,
        ),
    )
    assert result.accepted is True
    assert result.revision == revision + 1


def test_st_022_role_confirmation_timeout_confirms_all(core_at_role_reveal, actor):
    tick_to_role_reveal_deadline(core_at_role_reveal)
    assert core_at_role_reveal.state.phase is Phase.NIGHT_WOLF
    assert all(player.role_confirmed for player in core_at_role_reveal.state.players)
    for player in core_at_role_reveal.state.players:
        view = core_at_role_reveal.reconnect(actor(player.seat_id))
        assert view.role is player.role


def test_role_reveal_timeout_is_frozen_while_paused(
    core_at_role_reveal,
    host,
    envelope,
    submit,
):
    core = core_at_role_reveal
    host_actor = host()
    pause = envelope(
        host_actor,
        HostPauseCommand(reason="break"),
        expected_revision=core.state.revision,
    )
    assert submit(core, host_actor, pause).accepted is True
    assert core.state.deadline_at is not None

    timeout = SystemTimeout(
        timeout_id=deterministic_uuid(
            NAMESPACE_URL,
            core.state.room_id,
            core.state.revision,
            core.state.phase,
        ),
        room_id=core.state.room_id,
        expected_revision=core.state.revision,
        phase=core.state.phase,
        occurred_at=core.state.deadline_at,
    )
    outcome = apply_command(core.state, timeout, None, timeout.occurred_at)

    assert outcome.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert outcome.events == ()
    assert outcome.next_state == core.state


def test_role_reveal_timeout_before_deadline_is_rejected(core_at_role_reveal):
    core = core_at_role_reveal
    assert core.state.deadline_at is not None
    occurred_at = core.state.deadline_at - timedelta(seconds=1)
    timeout = SystemTimeout(
        timeout_id=deterministic_uuid(
            NAMESPACE_URL,
            core.state.room_id,
            core.state.revision,
            core.state.phase,
        ),
        room_id=core.state.room_id,
        expected_revision=core.state.revision,
        phase=core.state.phase,
        occurred_at=occurred_at,
    )
    outcome = apply_command(core.state, timeout, None, occurred_at)

    assert outcome.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert outcome.events == ()
    assert outcome.next_state == core.state


def test_pause_freezes_deadline_across_resume(
    core_at_role_reveal,
    host,
    envelope,
    submit,
):
    core = core_at_role_reveal
    host_actor = host()
    original_deadline = core.state.deadline_at
    assert original_deadline is not None
    paused_at = original_deadline - timedelta(seconds=1)
    resumed_at = original_deadline + timedelta(seconds=60)
    core.clock.set(paused_at)

    paused = submit(
        core,
        host_actor,
        envelope(
            host_actor,
            HostPauseCommand(reason="break"),
            expected_revision=core.state.revision,
            now=paused_at,
        ),
    )
    assert paused.accepted is True

    core.clock.set(resumed_at)
    resumed = submit(
        core,
        host_actor,
        envelope(
            host_actor,
            HostResumeCommand(),
            expected_revision=core.state.revision,
            now=resumed_at,
        ),
    )
    assert resumed.accepted is True
    assert core.state.phase is Phase.ROLE_REVEAL
    assert core.state.deadline_at == original_deadline + timedelta(seconds=61)

    core.tick()

    assert core.state.phase is Phase.ROLE_REVEAL
