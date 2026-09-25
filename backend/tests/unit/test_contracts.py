from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from pydantic import ValidationError

from werewolf_dm.domain.contracts import (
    EVENT_PAYLOAD_MODELS,
    AuthenticatedActor,
    CommandEnvelope,
    DomainEvent,
    HostVisibility,
    JoinRoomCommand,
    PlayersDiedPayload,
    PublicVisibility,
    ReadyChangedPayload,
    RoomJoinedPayload,
    SeatTally,
    SeatVisibility,
    VoteCommand,
    VoteRoundResolvedPayload,
    WitchActionPayload,
    build_event,
)
from werewolf_dm.domain.enums import CommandType, EventType, Role


def test_vis_015_extra_actor_field_rejected(actor, envelope):
    seat = actor(1)
    cmd = envelope(seat, JoinRoomCommand(seat_id=1, display_name="A"))
    with pytest.raises(ValidationError):
        CommandEnvelope(**cmd.model_dump(), actor_seat_id=5)


def test_player_command_union_uses_discriminator(actor, envelope):
    seat = actor(1)
    cmd = envelope(seat, VoteCommand(target_seat_id=2))
    assert cmd.payload.command_type is CommandType.VOTE
    with pytest.raises(ValidationError):
        CommandEnvelope(
            **{
                **cmd.model_dump(),
                "payload": {"command_type": "VOTE", "target_seat_id": "2"},
            }
        )


def test_actor_requires_host_to_omit_seat(room_id):
    with pytest.raises(ValidationError):
        AuthenticatedActor(actor_type="host", seat_id=1, room_id=room_id)


def test_every_event_type_has_a_payload_model():
    assert set(EVENT_PAYLOAD_MODELS) == set(EventType)


def test_event_payload_rejects_extra_fields():
    with pytest.raises(ValidationError):
        RoomJoinedPayload(seat_id=1, display_name="A", hidden_role="WEREWOLF")


def test_build_event_validates_payload_and_serializes_enum_values(room_id):
    cause_id = UUID("00000000-0000-0000-0000-000000000201")
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = build_event(
        cause_id=cause_id,
        event_ordinal=1,
        room_id=room_id,
        revision=3,
        event_type=EventType.ROOM_JOINED,
        visibility=PublicVisibility(),
        payload=RoomJoinedPayload(seat_id=1, display_name="A"),
        created_at=created_at,
    )
    assert event.event_id == uuid5(NAMESPACE_URL, f"{cause_id}:1:ROOM_JOINED")
    assert event.causation_id == cause_id
    assert event.correlation_id == cause_id
    assert event.fact_payload == {"seat_id": 1, "display_name": "A"}
    assert '"event_type":"ROOM_JOINED"' in event.model_dump_json()


def test_build_event_rejects_mismatched_payload(room_id):
    with pytest.raises(ValidationError):
        build_event(
            cause_id=uuid4(),
            event_ordinal=1,
            room_id=room_id,
            revision=1,
            event_type=EventType.ROOM_JOINED,
            visibility=PublicVisibility(),
            payload=ReadyChangedPayload(seat_id=1, ready=True),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_build_event_ids_are_deterministic_and_ordinal_sensitive(room_id):
    cause_id = UUID("00000000-0000-0000-0000-000000000202")
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    kwargs = {
        "cause_id": cause_id,
        "room_id": room_id,
        "revision": 1,
        "event_type": EventType.ROOM_JOINED,
        "visibility": PublicVisibility(),
        "payload": RoomJoinedPayload(seat_id=1, display_name="A"),
        "created_at": created_at,
    }
    first = build_event(event_ordinal=1, **kwargs)
    repeat = build_event(event_ordinal=1, **kwargs)
    second = build_event(event_ordinal=2, **kwargs)
    assert first.event_id == repeat.event_id
    assert first.event_id != second.event_id


def test_direct_event_rejects_mismatched_payload(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValidationError):
        DomainEvent(
            event_id=uuid4(),
            room_id=room_id,
            revision=1,
            event_type=EventType.ROOM_JOINED,
            visibility=PublicVisibility(),
            fact_payload={"seat_id": 1, "ready": True},
            causation_id=None,
            correlation_id=uuid4(),
            created_at=created_at,
        )


def test_direct_event_rejects_sensitive_payload_with_public_visibility(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValidationError):
        DomainEvent(
            event_id=uuid4(),
            room_id=room_id,
            revision=1,
            event_type=EventType.ROLE_ASSIGNED,
            visibility=PublicVisibility(),
            fact_payload={"seat_id": 1, "role": Role.SEER.value},
            causation_id=None,
            correlation_id=uuid4(),
            created_at=created_at,
        )


def test_direct_event_payload_is_immutable(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = DomainEvent(
        event_id=uuid4(),
        room_id=room_id,
        revision=1,
        event_type=EventType.PLAYERS_DIED,
        visibility=HostVisibility(),
        fact_payload=PlayersDiedPayload(seat_ids=(2,), cause="WOLF").model_dump(mode="json"),
        causation_id=None,
        correlation_id=uuid4(),
        created_at=created_at,
    )

    with pytest.raises(TypeError):
        event.fact_payload["cause"] = "POISON"


def test_direct_event_nested_payload_is_immutable(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = DomainEvent(
        event_id=uuid4(),
        room_id=room_id,
        revision=1,
        event_type=EventType.PLAYERS_DIED,
        visibility=HostVisibility(),
        fact_payload=PlayersDiedPayload(seat_ids=(2,), cause="WOLF").model_dump(mode="json"),
        causation_id=None,
        correlation_id=uuid4(),
        created_at=created_at,
    )

    with pytest.raises(TypeError):
        list.append(event.fact_payload["seat_ids"], 3)


def test_direct_event_payload_blocks_base_container_and_inplace_mutation(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = DomainEvent(
        event_id=uuid4(),
        room_id=room_id,
        revision=1,
        event_type=EventType.PLAYERS_DIED,
        visibility=HostVisibility(),
        fact_payload=PlayersDiedPayload(seat_ids=(2,), cause="WOLF").model_dump(mode="json"),
        causation_id=None,
        correlation_id=uuid4(),
        created_at=created_at,
    )

    with pytest.raises(TypeError):
        dict.__setitem__(event.fact_payload, "cause", "POISON")
    with pytest.raises(TypeError):
        event.fact_payload |= {"extra": True}
    with pytest.raises(TypeError):
        event.fact_payload["seat_ids"] += (3,)


def test_direct_event_model_copy_cannot_bypass_payload_validation(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = DomainEvent(
        event_id=uuid4(),
        room_id=room_id,
        revision=1,
        event_type=EventType.PLAYERS_DIED,
        visibility=PublicVisibility(),
        fact_payload=PlayersDiedPayload(seat_ids=(2,)).model_dump(mode="json"),
        causation_id=None,
        correlation_id=uuid4(),
        created_at=created_at,
    )

    with pytest.raises(ValidationError):
        event.model_copy(
            update={
                "fact_payload": {
                    "seat_ids": [2],
                    "cause": "WOLF",
                }
            }
        )


def test_seat_visibility_must_match_payload_actor(room_id):
    with pytest.raises(ValidationError):
        DomainEvent(
            event_id=uuid4(),
            room_id=room_id,
            revision=1,
            event_type=EventType.ROLE_ASSIGNED,
            visibility=SeatVisibility(seat_id=1),
            fact_payload={"seat_id": 2, "role": Role.SEER.value},
            causation_id=None,
            correlation_id=uuid4(),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_witch_action_seat_visibility_must_match_payload_witch(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValidationError):
        DomainEvent(
            event_id=uuid4(),
            room_id=room_id,
            revision=1,
            event_type=EventType.WITCH_ACTION_RECORDED,
            visibility=SeatVisibility(seat_id=2),
            fact_payload=WitchActionPayload(
                witch_seat_id=1,
                action="SKIP",
                target_seat_id=None,
            ).model_dump(mode="json"),
            causation_id=None,
            correlation_id=uuid4(),
            created_at=created_at,
        )


@pytest.mark.parametrize("witch_seat_id", [True, 1.0])
def test_witch_action_payload_rejects_coerced_witch_seat(room_id, witch_seat_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValidationError):
        DomainEvent(
            event_id=uuid4(),
            room_id=room_id,
            revision=1,
            event_type=EventType.WITCH_ACTION_RECORDED,
            visibility=SeatVisibility(seat_id=1),
            fact_payload={
                "witch_seat_id": witch_seat_id,
                "action": "SKIP",
                "target_seat_id": None,
            },
            causation_id=None,
            correlation_id=uuid4(),
            created_at=created_at,
        )


def test_witch_action_model_copy_cannot_forge_seat_visibility(room_id):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = DomainEvent(
        event_id=uuid4(),
        room_id=room_id,
        revision=1,
        event_type=EventType.WITCH_ACTION_RECORDED,
        visibility=SeatVisibility(seat_id=1),
        fact_payload=WitchActionPayload(
            witch_seat_id=1,
            action="SKIP",
            target_seat_id=None,
        ).model_dump(mode="json"),
        causation_id=None,
        correlation_id=uuid4(),
        created_at=created_at,
    )

    with pytest.raises(ValidationError):
        event.model_copy(update={"visibility": SeatVisibility(seat_id=2)})


def test_vote_round_payload_rejects_duplicate_candidates():
    with pytest.raises(ValidationError):
        VoteRoundResolvedPayload(
            round_id=uuid4(),
            tallies=(
                SeatTally(seat_id=1, votes=2),
                SeatTally(seat_id=1, votes=2),
                SeatTally(seat_id=2, votes=2),
            ),
            exiled_seat_id=None,
            tie=True,
        )
