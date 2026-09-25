from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from werewolf_dm.domain.contracts import (
    CommandEnvelope,
    CommandResult,
    SetReadyCommand,
)
from werewolf_dm.domain.enums import Phase, Role
from werewolf_dm.domain.model import (
    Player,
    PotionState,
    PrivateFact,
    RoleAssignment,
    Vote,
    VoteRound,
)


def test_player_is_strict_and_frozen():
    player = Player(
        seat_id=1,
        display_name="A",
        alive=True,
        connected=True,
        ready=True,
        role=None,
        role_confirmed=False,
    )
    assert player.model_copy(update={"alive": False}).alive is False
    with pytest.raises(ValidationError):
        Player(**{**player.model_dump(), "alive": 1})
    with pytest.raises(ValidationError):
        Player(**player.model_dump(), nickname="extra")


def test_role_and_vote_value_objects():
    assignment = RoleAssignment(seat_id=3, role=Role.WEREWOLF)
    potions = PotionState(antidote_available=True, poison_available=True)
    vote = Vote(voter_seat_id=1, target_seat_id=3)
    round_ = VoteRound(
        round_id=UUID("00000000-0000-0000-0000-000000000101"),
        round_index=1,
        phase=Phase.DAY_VOTE,
        candidate_seat_ids=(),
        eligible_voter_ids=(1, 2),
    )
    assert assignment.role is Role.WEREWOLF
    assert potions.poison_available is True
    assert vote.target_seat_id == 3
    assert round_.votes == ()


def test_private_fact_nested_payload_is_immutable():
    fact = PrivateFact(
        fact_id=uuid4(),
        event_id=uuid4(),
        recipient_seat_id=1,
        revision=1,
        fact_type="WOLF_TEAM",
        payload={"seat_ids": [1, 2]},
    )

    with pytest.raises(TypeError):
        list.append(fact.payload["seat_ids"], 3)


def test_private_fact_model_copy_payload_is_frozen():
    fact = PrivateFact(
        fact_id=uuid4(),
        event_id=uuid4(),
        recipient_seat_id=1,
        revision=1,
        fact_type="WOLF_TEAM",
        payload={"seat_ids": [1, 2]},
    )

    copied = fact.model_copy(update={"payload": {"seat_ids": [1, 2]}})

    with pytest.raises(TypeError):
        copied.payload["seat_ids"] += (3,)


def test_strict_model_copy_revalidates_invariants():
    vote_round = VoteRound(
        round_id=UUID("00000000-0000-0000-0000-000000000101"),
        round_index=1,
        phase=Phase.DAY_VOTE,
        candidate_seat_ids=(),
        eligible_voter_ids=(1, 2),
    )

    with pytest.raises(ValidationError):
        vote_round.model_copy(update={"eligible_voter_ids": (1, 1)})


def test_strict_model_copy_rejects_type_coercion():
    player = Player(
        seat_id=1,
        display_name="A",
        alive=True,
        connected=True,
        ready=True,
        role=None,
        role_confirmed=False,
    )

    with pytest.raises(ValidationError):
        player.model_copy(update={"alive": 1})
    with pytest.raises(ValidationError):
        player.model_copy(update={"alive": 1}, deep=True)
    with pytest.raises(ValidationError):
        player.model_copy(update={"role": "SEER"})


def test_strict_model_copy_without_update_revalidates_constructed_model():
    player = Player.model_construct(
        seat_id=1,
        display_name="A",
        alive=1,
        connected=True,
        ready=True,
        role=None,
        role_confirmed=False,
    )

    with pytest.raises(ValidationError):
        player.model_copy(deep=True)


def test_strict_model_copy_revalidates_constructed_nested_payload():
    room_id = uuid4()
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=room_id,
        expected_revision=0,
        issued_at=datetime(2026, 9, 24, tzinfo=UTC),
        payload=SetReadyCommand(command_type="SET_READY", ready=True),
    )
    forged_payload = SetReadyCommand.model_construct(
        command_type="SET_READY",
        ready=1,
    )

    with pytest.raises(ValidationError):
        envelope.model_copy(update={"payload": forged_payload})
    with pytest.raises(ValidationError):
        envelope.model_copy(update={"payload": forged_payload}, deep=True)


def test_revalidate_rejects_uuid_subclass_inside_container():
    class UUIDSubclass(UUID):
        pass

    result = CommandResult.model_construct(
        command_id=uuid4(),
        accepted=True,
        revision=0,
        event_ids=(UUIDSubclass(str(uuid4())),),
    )

    with pytest.raises(ValidationError):
        CommandResult.revalidate(result)
