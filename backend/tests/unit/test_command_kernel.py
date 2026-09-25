from uuid import UUID, uuid4

import pytest

from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    CommandEnvelope,
    HostPauseCommand,
    JoinRoomCommand,
    SetReadyCommand,
    VoteCommand,
)


def test_idem_001_same_command_id_returns_first_result(core, actor, envelope):
    cmd = envelope(actor(1), JoinRoomCommand(seat_id=1, display_name="A"))
    first = core.submit(cmd, actor(1))
    second = core.submit(cmd, actor(1))
    assert first == second
    assert core.state.revision == first.revision


def test_idem_002_stale_revision_conflicts_without_cache(core, actor, envelope):
    core.submit(
        envelope(actor(1), JoinRoomCommand(seat_id=1, display_name="A")),
        actor(1),
    )
    stale = envelope(
        actor(2),
        JoinRoomCommand(seat_id=2, display_name="B"),
        expected_revision=0,
    )
    result = core.submit(stale, actor(2))
    assert result.accepted is False
    assert result.error_code.value == "REVISION_CONFLICT"
    assert stale.command_id not in core.command_dedupe_cache


def test_idem_003_revision_conflict_retries_with_new_command(
    core_with_two_joined_players,
    actor,
    envelope,
):
    core = core_with_two_joined_players
    revision = core.state.revision
    first = envelope(
        actor(1),
        SetReadyCommand(ready=True),
        expected_revision=revision,
    )
    second = envelope(
        actor(2),
        SetReadyCommand(ready=True),
        expected_revision=revision,
    )
    assert core.submit(first, actor(1)).accepted is True
    conflict = core.submit(second, actor(2))
    assert conflict.error_code.value == "REVISION_CONFLICT"
    retry = envelope(
        actor(2),
        SetReadyCommand(ready=True),
        expected_revision=core.state.revision,
    )
    assert core.submit(retry, actor(2)).accepted is True
    assert {player.seat_id for player in core.state.players if player.ready} == {1, 2}


def test_idem_004_reconnect_returns_latest_authorized_view(core, actor):
    view = core.reconnect(actor(1))
    assert view.room_id == core.state.room_id
    assert view.revision == core.state.revision
    assert view.seat_id == 1


def test_idem_005_duplicate_host_pause_is_idempotent(core, host, envelope):
    host_actor = host()
    first = core.submit(
        envelope(
            host_actor,
            HostPauseCommand(reason="break"),
            expected_revision=core.state.revision,
        ),
        host_actor,
    )
    revision = core.state.revision
    second = core.submit(
        envelope(
            host_actor,
            HostPauseCommand(reason="break"),
            expected_revision=revision,
        ),
        host_actor,
    )
    assert first.accepted is True
    assert second.accepted is True
    assert core.state.revision == revision


def test_idem_001_rejected_command_id_is_cached(core, actor, envelope):
    rejected = envelope(
        actor(1),
        VoteCommand(target_seat_id=2),
        expected_revision=core.state.revision,
    )
    first = core.submit(rejected, actor(1))
    join = envelope(
        actor(1),
        JoinRoomCommand(seat_id=1, display_name="A"),
        expected_revision=core.state.revision,
    )
    core.submit(join, actor(1))
    second = core.submit(rejected, actor(1))
    assert first == second
    assert second.error_code.value == "ILLEGAL_PHASE"


def test_tick_returns_applied_timeout_results(core_at_role_reveal):
    core = core_at_role_reveal
    clock = core.clock
    deadline = core.state.deadline_at
    assert deadline is not None
    clock.set(deadline)

    results = core.tick()

    assert results
    assert all(result.accepted for result in results)


def test_tick_returns_no_result_before_deadline(core_at_role_reveal):
    core = core_at_role_reveal

    results = core.tick()

    assert results == ()


def test_gamecore_state_and_events_are_read_only(core):
    original_state = core.state

    with pytest.raises(AttributeError):
        core.state = original_state
    with pytest.raises(AttributeError):
        core.events = tuple(core.events)
    assert isinstance(core.events, tuple)
    with pytest.raises(AttributeError):
        core.events.append(None)


def test_submit_revalidates_model_constructed_envelope(core, actor):
    room_id = core.state.room_id
    envelope = CommandEnvelope.model_construct(
        schema_version="command.v1",
        command_id=uuid4(),
        room_id=room_id,
        expected_revision=0.0,
        issued_at=core.clock(),
        payload=JoinRoomCommand(seat_id=1, display_name="A"),
    )
    revision = core.state.revision

    with pytest.raises(ValueError):
        core.submit(envelope, actor(1))

    assert core.state.revision == revision
    assert core.state.players == ()
    assert core.events == ()


def test_submit_revalidates_model_constructed_actor(core):
    actor = AuthenticatedActor.model_construct(
        actor_type="seat",
        seat_id=True,
        room_id=core.state.room_id,
    )
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=core.state.room_id,
        expected_revision=core.state.revision,
        issued_at=core.clock(),
        payload=JoinRoomCommand(seat_id=1, display_name="A"),
    )

    with pytest.raises(ValueError):
        core.submit(envelope, actor)

    assert core.state.revision == 0
    assert core.state.players == ()
    assert core.events == ()


def test_submit_rejects_uuid_subclass_room_id(core):
    class EqualUUID(UUID):
        def __eq__(self, other):
            return True

        __hash__ = UUID.__hash__

    forged_actor = AuthenticatedActor.model_construct(
        actor_type="seat",
        seat_id=1,
        room_id=EqualUUID("00000000-0000-0000-0000-0000000000bb"),
    )
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=core.state.room_id,
        expected_revision=core.state.revision,
        issued_at=core.clock(),
        payload=JoinRoomCommand(seat_id=1, display_name="A"),
    )

    with pytest.raises(ValueError):
        core.submit(envelope, forged_actor)

    assert core.state.revision == 0
    assert core.state.players == ()
    assert core.events == ()


def test_cached_command_result_is_scoped_to_original_actor(core, actor, envelope):
    original = envelope(
        actor(1),
        JoinRoomCommand(seat_id=1, display_name="A"),
    )
    first = core.submit(original, actor(1))
    assert first.accepted is True

    same_room_attacker = core.submit(
        CommandEnvelope(
            command_id=original.command_id,
            room_id=core.state.room_id,
            expected_revision=core.state.revision,
            issued_at=core.clock(),
            payload=JoinRoomCommand(seat_id=1, display_name="A"),
        ),
        actor(2),
    )
    assert same_room_attacker.accepted is False
    assert same_room_attacker.error_code.value == "ACTOR_NOT_AUTHORIZED"
    assert same_room_attacker.event_ids == ()

    foreign_actor = AuthenticatedActor(
        actor_type="seat",
        seat_id=1,
        room_id=uuid4(),
    )
    foreign_room_attacker = core.submit(
        CommandEnvelope(
            command_id=original.command_id,
            room_id=core.state.room_id,
            expected_revision=core.state.revision,
            issued_at=core.clock(),
            payload=JoinRoomCommand(seat_id=1, display_name="A"),
        ),
        foreign_actor,
    )
    assert foreign_room_attacker.accepted is False
    assert foreign_room_attacker.error_code.value == "ACTOR_NOT_AUTHORIZED"
    assert foreign_room_attacker.event_ids == ()
