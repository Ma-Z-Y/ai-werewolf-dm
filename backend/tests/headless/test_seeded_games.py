from datetime import UTC, datetime
from uuid import uuid4

import pytest

from werewolf_dm.application.core import FrozenClock, GameCore
from werewolf_dm.application.simulation import record_invariants, run_seeded_game, seat_actor
from werewolf_dm.domain.contracts import PlayersDiedPayload, PublicVisibility, build_event
from werewolf_dm.domain.enums import EventType, Faction, Phase
from werewolf_dm.domain.visibility import project_public_view, project_seat_view


@pytest.mark.parametrize("seed", range(1001, 1101))
def test_100_seeded_games_reach_game_end(seed: int) -> None:
    record = run_seeded_game(seed)

    assert record.final_state.phase is Phase.GAME_END
    assert record.final_state.winner in {Faction.GOOD, Faction.WEREWOLF}
    assert record.revision_count <= 500
    assert record.state_hashes
    assert record.policy_kinds == {"random", "conservative", "aggressive"}
    assert record.violations == ()
    assert record.projection_checks >= record.revision_count


@pytest.mark.parametrize("seed", [1001, 1017, 1053])
def test_same_seed_replay_is_hash_identical(seed: int) -> None:
    first = run_seeded_game(seed)
    second = run_seeded_game(seed)

    assert first.state_hashes == second.state_hashes


def test_public_and_private_projections_are_accessible() -> None:
    record = run_seeded_game(1001)

    assert project_public_view(record.final_state).room_id == record.room_id
    for seat_id in range(1, 7):
        assert (
            project_seat_view(
                record.final_state,
                seat_id,
                seat_actor(record.room_id, seat_id),
            ).seat_id
            == seat_id
        )


def test_record_invariants_detects_duplicate_death_events() -> None:
    room_id = uuid4()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    core = GameCore.new_room(room_id, seed=1001, clock=FrozenClock(now))
    events = tuple(
        build_event(
            cause_id=uuid4(),
            event_ordinal=ordinal,
            room_id=room_id,
            revision=ordinal,
            event_type=EventType.PLAYERS_DIED,
            visibility=PublicVisibility(),
            payload=PlayersDiedPayload(seat_ids=(2,)),
            created_at=now,
        )
        for ordinal in (1, 2)
    )
    violations: list[str] = []

    record_invariants(core, [], violations, events=events)

    assert any("duplicate death" in violation for violation in violations)
