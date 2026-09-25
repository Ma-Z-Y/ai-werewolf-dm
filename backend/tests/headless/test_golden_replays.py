import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import werewolf_dm.application.simulation as simulation
from werewolf_dm.application.core import FrozenClock, GameCore
from werewolf_dm.application.simulation import (
    GOLDEN_REQUIRED_ASSERTIONS,
    GoldenAssertion,
    GoldenDocument,
    _semantic_assertions_pass,
    run_golden_replay,
)
from werewolf_dm.domain.contracts import (
    DomainEvent,
    FactionVisibility,
    GameEndedPayload,
    NoExilePayload,
    PlayerExiledPayload,
    PublicVisibility,
    VoteRoundResolvedPayload,
    WolfNominationPayload,
    WolfTargetLockedPayload,
    build_event,
)
from werewolf_dm.domain.enums import EventType, Faction
from werewolf_dm.domain.model import SeatTally

ROOM_ID = UUID("00000000-0000-0000-0000-000000000099")
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "scenario",
    [
        "first_night_peaceful",
        "first_night_one_death",
        "antidote_saves_kill",
        "poison_independent_death",
        "wolf_consensus_and_empty_knife",
        "pk_two_tie",
        "pk_three_tie",
        "exile_last_wolf",
        "wolves_reach_parity",
    ],
)
def test_golden_replay(scenario: str) -> None:
    assert run_golden_replay(scenario)


def test_pk_three_tie_declares_three_candidate_pk_tie() -> None:
    path = Path(__file__).with_name("golden") / "pk_three_tie.json"
    document = GoldenDocument.model_validate_json(path.read_text(encoding="utf-8"))

    assert "PK_THREE_TIE_NO_EXILE" in document.expected.semantic_assertions
    assert json.loads(path.read_text(encoding="utf-8"))["expected"]["semantic_assertions"] == [
        "PK_THREE_TIE_NO_EXILE"
    ]


@pytest.mark.parametrize(
    "scenario",
    [
        "first_night_peaceful",
        "first_night_one_death",
        "antidote_saves_kill",
        "poison_independent_death",
        "wolf_consensus_and_empty_knife",
        "pk_two_tie",
        "pk_three_tie",
        "exile_last_wolf",
        "wolves_reach_parity",
    ],
)
def test_every_golden_declares_semantic_assertion(scenario: str) -> None:
    path = Path(__file__).with_name("golden") / f"{scenario}.json"
    document = GoldenDocument.model_validate_json(path.read_text(encoding="utf-8"))

    assert document.expected.semantic_assertions


def test_empty_semantic_assertions_do_not_pass() -> None:
    assert _semantic_assertions_pass((), ()) is False


def test_unknown_semantic_assertion_does_not_pass() -> None:
    assertions = cast(tuple[GoldenAssertion, ...], ("NOT_A_REAL_ASSERTION",))

    assert _semantic_assertions_pass((), assertions) is False


def test_wolves_reach_parity_requires_actual_parity() -> None:
    core = GameCore.new_room(ROOM_ID, 101, FrozenClock(CREATED_AT))
    events = (
        build_event(
            cause_id=UUID("00000000-0000-0000-0000-000000000098"),
            event_ordinal=1,
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.GAME_ENDED,
            visibility=PublicVisibility(),
            payload=GameEndedPayload(winner=Faction.WEREWOLF),
            created_at=CREATED_AT,
        ),
    )

    assert (
        _semantic_assertions_pass(
            events,
            ("WOLVES_REACH_PARITY",),
            state=core.state,
        )
        is False
    )


def test_exile_last_wolf_requires_exiled_wolf() -> None:
    core = GameCore.new_room(ROOM_ID, 101, FrozenClock(CREATED_AT))
    events = (
        build_event(
            cause_id=UUID("00000000-0000-0000-0000-000000000097"),
            event_ordinal=1,
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.PLAYER_EXILED,
            visibility=PublicVisibility(),
            payload=PlayerExiledPayload(seat_id=1),
            created_at=CREATED_AT,
        ),
        build_event(
            cause_id=UUID("00000000-0000-0000-0000-000000000096"),
            event_ordinal=2,
            room_id=ROOM_ID,
            revision=2,
            event_type=EventType.GAME_ENDED,
            visibility=PublicVisibility(),
            payload=GameEndedPayload(winner=Faction.GOOD),
            created_at=CREATED_AT,
        ),
    )

    assert (
        _semantic_assertions_pass(
            events,
            ("EXILE_LAST_WOLF",),
            state=core.state,
        )
        is False
    )


def test_golden_runner_rejects_empty_semantic_document(tmp_path, monkeypatch) -> None:
    source = Path(__file__).with_name("golden") / "pk_three_tie.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    document["expected"]["semantic_assertions"] = []
    invalid_path = tmp_path / "pk_three_tie.json"
    invalid_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(simulation, "_golden_path", lambda _name: invalid_path)

    assert simulation.run_golden_replay("pk_three_tie") is False


def test_golden_document_rejects_scenario_mismatched_semantics() -> None:
    path = Path(__file__).with_name("golden") / "pk_two_tie.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["expected"]["semantic_assertions"] = ["PK_THREE_TIE_NO_EXILE"]

    with pytest.raises(ValidationError):
        GoldenDocument.model_validate_json(json.dumps(document))


def test_golden_document_rejects_unknown_scenario() -> None:
    path = Path(__file__).with_name("golden") / "pk_two_tie.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["scenario"] = "unknown_scenario"

    with pytest.raises(ValidationError):
        GoldenDocument.model_validate_json(json.dumps(document))


def test_golden_runner_rejects_scenario_mismatched_semantics(tmp_path, monkeypatch) -> None:
    source = Path(__file__).with_name("golden") / "pk_two_tie.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    document["expected"]["semantic_assertions"] = ["PK_THREE_TIE_NO_EXILE"]
    invalid_path = tmp_path / "pk_two_tie.json"
    invalid_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(simulation, "_golden_path", lambda _name: invalid_path)

    assert simulation.run_golden_replay("pk_two_tie") is False


def test_golden_runner_rejects_scenario_identity_mismatch(tmp_path, monkeypatch) -> None:
    source = Path(__file__).with_name("golden") / "pk_three_tie.json"
    invalid_path = tmp_path / "pk_two_tie.json"
    invalid_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(simulation, "_golden_path", lambda _name: invalid_path)

    assert simulation.run_golden_replay("pk_two_tie") is False


def test_golden_scenarios_use_non_substitutable_assertions() -> None:
    required_values = tuple(GOLDEN_REQUIRED_ASSERTIONS.values())

    assert len(set(required_values)) == len(required_values)


def test_wolf_consensus_assertion_requires_same_target_consensus() -> None:
    events = (
        build_event(
            cause_id=uuid4(),
            event_ordinal=1,
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.WOLF_NOMINATION_RECORDED,
            visibility=FactionVisibility(faction=Faction.WEREWOLF),
            payload=WolfNominationPayload(seat_id=5, target_seat_id=2),
            created_at=CREATED_AT,
        ),
        build_event(
            cause_id=uuid4(),
            event_ordinal=2,
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.WOLF_NOMINATION_RECORDED,
            visibility=FactionVisibility(faction=Faction.WEREWOLF),
            payload=WolfNominationPayload(seat_id=6, target_seat_id=3),
            created_at=CREATED_AT,
        ),
        build_event(
            cause_id=uuid4(),
            event_ordinal=3,
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.WOLF_TARGET_LOCKED,
            visibility=FactionVisibility(faction=Faction.WEREWOLF),
            payload=WolfTargetLockedPayload(target_seat_id=None),
            created_at=CREATED_AT,
        ),
    )

    assert (
        _semantic_assertions_pass(
            events,
            ("WOLF_CONSENSUS_EMPTY_KNIFE",),
        )
        is False
    )


def test_second_three_way_pk_requires_the_last_resolution_to_tie() -> None:
    events = (
        DomainEvent.model_construct(
            event_id=uuid4(),
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.VOTE_ROUND_RESOLVED,
            visibility=PublicVisibility(),
            fact_payload=VoteRoundResolvedPayload(
                round_id=uuid4(),
                tallies=(
                    SeatTally(seat_id=1, votes=2),
                    SeatTally(seat_id=2, votes=2),
                    SeatTally(seat_id=3, votes=2),
                ),
                exiled_seat_id=None,
                tie=True,
            ).model_dump(mode="json"),
            causation_id=None,
            correlation_id=uuid4(),
            created_at=CREATED_AT,
        ),
        DomainEvent.model_construct(
            event_id=uuid4(),
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.VOTE_ROUND_RESOLVED,
            visibility=PublicVisibility(),
            fact_payload=VoteRoundResolvedPayload(
                round_id=uuid4(),
                tallies=(
                    SeatTally(seat_id=1, votes=1),
                    SeatTally(seat_id=2, votes=1),
                ),
                exiled_seat_id=None,
                tie=True,
            ).model_dump(mode="json"),
            causation_id=None,
            correlation_id=uuid4(),
            created_at=CREATED_AT,
        ),
        DomainEvent.model_construct(
            event_id=uuid4(),
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.NO_EXILE,
            visibility=PublicVisibility(),
            fact_payload=NoExilePayload(reason="PK_TIE").model_dump(mode="json"),
            causation_id=None,
            correlation_id=uuid4(),
            created_at=CREATED_AT,
        ),
    )

    assert (
        _semantic_assertions_pass(
            events,
            ("PK_THREE_TIE_NO_EXILE",),
        )
        is False
    )


def test_three_way_pk_requires_three_positive_equal_tallies() -> None:
    events = (
        DomainEvent.model_construct(
            event_id=uuid4(),
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.VOTE_ROUND_RESOLVED,
            visibility=PublicVisibility(),
            fact_payload=VoteRoundResolvedPayload(
                round_id=uuid4(),
                tallies=(
                    SeatTally(seat_id=1, votes=1),
                    SeatTally(seat_id=2, votes=1),
                    SeatTally(seat_id=3, votes=0),
                ),
                exiled_seat_id=None,
                tie=True,
            ).model_dump(mode="json"),
            causation_id=None,
            correlation_id=uuid4(),
            created_at=CREATED_AT,
        ),
        DomainEvent.model_construct(
            event_id=uuid4(),
            room_id=ROOM_ID,
            revision=2,
            event_type=EventType.NO_EXILE,
            visibility=PublicVisibility(),
            fact_payload=NoExilePayload(reason="PK_TIE").model_dump(mode="json"),
            causation_id=None,
            correlation_id=uuid4(),
            created_at=CREATED_AT,
        ),
    )

    assert _semantic_assertions_pass(events, ("PK_THREE_TIE_NO_EXILE",)) is False


def test_three_way_pk_requires_distinct_candidates() -> None:
    events = (
        DomainEvent.model_construct(
            event_id=uuid4(),
            room_id=ROOM_ID,
            revision=1,
            event_type=EventType.VOTE_ROUND_RESOLVED,
            visibility=PublicVisibility(),
            fact_payload={
                "round_id": str(uuid4()),
                "tallies": [
                    {"seat_id": 1, "votes": 2},
                    {"seat_id": 1, "votes": 2},
                    {"seat_id": 2, "votes": 2},
                ],
                "exiled_seat_id": None,
                "tie": True,
            },
            causation_id=None,
            correlation_id=uuid4(),
            created_at=CREATED_AT,
        ),
        DomainEvent.model_construct(
            event_id=uuid4(),
            room_id=ROOM_ID,
            revision=2,
            event_type=EventType.NO_EXILE,
            visibility=PublicVisibility(),
            fact_payload=NoExilePayload(reason="PK_TIE").model_dump(mode="json"),
            causation_id=None,
            correlation_id=uuid4(),
            created_at=CREATED_AT,
        ),
    )

    assert _semantic_assertions_pass(events, ("PK_THREE_TIE_NO_EXILE",)) is False
