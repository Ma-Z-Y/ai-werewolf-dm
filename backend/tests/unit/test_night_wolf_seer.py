import pytest

from tests.factories import (
    Scenario,
    envelope_for,
    faction_of,
    tick_to_seer_deadline,
    tick_to_wolf_deadline,
)
from werewolf_dm.domain.contracts import (
    CommandErrorCode,
    SeerInspectCommand,
    WolfNominateKillCommand,
)
from werewolf_dm.domain.enums import EventType, Faction, Phase, Role


def _submit_wolf_nomination(
    scenario: Scenario,
    actor,
    wolf_id: int,
    target_seat_id: int,
):
    return scenario.core.submit(
        envelope_for(
            scenario.core,
            wolf_id,
            WolfNominateKillCommand(target_seat_id=target_seat_id),
        ),
        actor(wolf_id),
    )


def _submit_seer_inspection(
    scenario: Scenario,
    actor,
    target_seat_id: int,
):
    return scenario.core.submit(
        envelope_for(
            scenario.core,
            scenario.seer_id,
            SeerInspectCommand(target_seat_id=target_seat_id),
        ),
        actor(scenario.seer_id),
    )


def test_st_003_wolf_consensus_locks_and_advances(core_at_wolf, actor):
    scenario = core_at_wolf
    target = scenario.good_ids[0]

    for wolf_id in scenario.wolf_ids:
        assert _submit_wolf_nomination(scenario, actor, wolf_id, target).accepted

    assert scenario.core.state.wolf_decision.locked is True
    assert scenario.core.state.wolf_decision.target_seat_id == target
    assert scenario.core.state.phase is Phase.NIGHT_SEER
    assert EventType.WOLF_TARGET_LOCKED in {event.event_type for event in scenario.core.events}


def test_st_004_wolf_disagreement_stays_in_wolf_phase(core_at_wolf, actor):
    scenario = core_at_wolf

    assert _submit_wolf_nomination(
        scenario,
        actor,
        scenario.wolf_ids[0],
        scenario.good_ids[0],
    ).accepted
    assert _submit_wolf_nomination(
        scenario,
        actor,
        scenario.wolf_ids[1],
        scenario.good_ids[1],
    ).accepted

    assert scenario.core.state.phase is Phase.NIGHT_WOLF
    assert scenario.core.state.wolf_decision.locked is False
    assert scenario.core.state.wolf_decision.target_seat_id is None


def test_wolf_latest_nomination_replaces_prior_choice(core_at_wolf, actor):
    scenario = core_at_wolf
    agreed_target = scenario.good_ids[1]

    _submit_wolf_nomination(
        scenario,
        actor,
        scenario.wolf_ids[0],
        scenario.good_ids[0],
    )
    _submit_wolf_nomination(
        scenario,
        actor,
        scenario.wolf_ids[1],
        agreed_target,
    )
    assert _submit_wolf_nomination(
        scenario,
        actor,
        scenario.wolf_ids[0],
        agreed_target,
    ).accepted

    assert scenario.core.state.phase is Phase.NIGHT_SEER
    assert scenario.core.state.wolf_decision.target_seat_id == agreed_target


def test_repeated_wolf_nomination_is_idempotent(core_at_wolf, actor):
    scenario = core_at_wolf
    wolf_id = scenario.wolf_ids[0]
    target = scenario.good_ids[0]

    first = _submit_wolf_nomination(scenario, actor, wolf_id, target)
    revision = scenario.core.state.revision
    second = _submit_wolf_nomination(scenario, actor, wolf_id, target)

    assert first.accepted is True
    assert second.accepted is True
    assert scenario.core.state.revision == revision


def test_st_005_wolf_timeout_empty_knife(core_at_wolf):
    scenario = core_at_wolf

    tick_to_wolf_deadline(scenario.core)

    assert scenario.core.state.phase is Phase.NIGHT_SEER
    assert scenario.core.state.wolf_decision.target_seat_id is None
    assert scenario.core.state.wolf_decision.locked is True
    assert EventType.TIMEOUT_APPLIED in {event.event_type for event in scenario.core.events}


def test_st_006_seer_result_survives_wolf_to_seer_phase_advance(core_at_seer, actor):
    scenario, target = core_at_seer

    result = _submit_seer_inspection(scenario, actor, target)

    assert result.accepted is True
    assert scenario.core.state.phase is Phase.NIGHT_WITCH
    assert len(scenario.core.state.seer_checks) == 1
    checked = scenario.core.state.seer_checks[0]
    assert checked.target_seat_id == target
    assert checked.faction is faction_of(scenario, target)
    assert scenario.core.state.seer_checks == (checked,)


def test_seer_can_inspect_living_wolf(core_at_seer, actor):
    scenario, target = core_at_seer

    assert _submit_seer_inspection(scenario, actor, target).accepted is True

    assert scenario.core.state.seer_checks[-1].faction is Faction.WEREWOLF


def test_st_012_dead_player_night_action_rejected(
    core_with_one_living_wolf_and_dead_good,
    actor,
):
    scenario = core_with_one_living_wolf_and_dead_good
    dead_wolf_id = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.role is Role.WEREWOLF and not player.alive
    )
    revision = scenario.core.state.revision

    result = _submit_wolf_nomination(
        scenario,
        actor,
        dead_wolf_id,
        scenario.good_ids[0],
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.PLAYER_DEAD
    assert scenario.core.state.revision == revision


def test_st_015_single_living_wolf_locks_immediately(
    core_with_one_living_wolf_and_dead_good,
    actor,
):
    scenario = core_with_one_living_wolf_and_dead_good
    living_wolf_id = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.role is Role.WEREWOLF and player.alive
    )
    target = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.alive and player.role is not Role.WEREWOLF
    )

    assert _submit_wolf_nomination(scenario, actor, living_wolf_id, target).accepted

    assert scenario.core.state.wolf_decision.locked is True
    assert scenario.core.state.wolf_decision.target_seat_id == target
    assert scenario.core.state.phase is Phase.NIGHT_SEER


def test_st_016_single_living_wolf_timeout_empty_knife(
    core_with_one_living_wolf_and_dead_good,
):
    scenario = core_with_one_living_wolf_and_dead_good

    tick_to_wolf_deadline(scenario.core)

    assert scenario.core.state.phase is Phase.NIGHT_SEER
    assert scenario.core.state.wolf_decision.target_seat_id is None


def test_st_017_living_seer_timeout_abandons_check(core_at_seer):
    scenario, _ = core_at_seer

    tick_to_seer_deadline(scenario.core)

    assert scenario.core.state.phase is Phase.NIGHT_WITCH
    assert scenario.core.state.seer_checks == ()
    assert any(
        event.event_type is EventType.TIMEOUT_APPLIED
        and event.fact_payload["timeout_reason"] == "SEER"
        for event in scenario.core.events
    )


def test_dead_seer_skips_to_witch_phase(
    core_with_one_living_wolf_and_dead_seer,
    actor,
):
    scenario = core_with_one_living_wolf_and_dead_seer
    target = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.alive and player.role is not Role.WEREWOLF
    )

    living_wolf_id = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.role is Role.WEREWOLF and player.alive
    )
    checks_before = scenario.core.state.seer_checks
    assert _submit_wolf_nomination(scenario, actor, living_wolf_id, target).accepted

    assert scenario.core.state.phase is Phase.NIGHT_WITCH
    assert scenario.core.state.seer_checks == checks_before


@pytest.mark.parametrize("invalid_target", ["self", "teammate"])
def test_wolf_cannot_target_self_or_teammate(core_at_wolf, actor, invalid_target):
    scenario = core_at_wolf
    wolf_id = scenario.wolf_ids[0]
    target = wolf_id if invalid_target == "self" else scenario.wolf_ids[1]
    revision = scenario.core.state.revision

    result = _submit_wolf_nomination(scenario, actor, wolf_id, target)

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.INVALID_TARGET
    assert scenario.core.state.revision == revision


def test_wolf_cannot_target_dead_good_player(
    core_with_one_living_wolf_and_dead_good,
    actor,
):
    scenario = core_with_one_living_wolf_and_dead_good
    dead_target = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.role is not Role.WEREWOLF and not player.alive
    )
    living_wolf_id = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.role is Role.WEREWOLF and player.alive
    )

    result = _submit_wolf_nomination(
        scenario,
        actor,
        living_wolf_id,
        dead_target,
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.INVALID_TARGET


def test_non_wolf_cannot_nominate(core_at_wolf, actor):
    scenario = core_at_wolf

    result = _submit_wolf_nomination(
        scenario,
        actor,
        scenario.seer_id,
        scenario.good_ids[0],
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED


def test_seer_cannot_inspect_self(core_at_seer, actor):
    scenario, _ = core_at_seer
    revision = scenario.core.state.revision

    result = _submit_seer_inspection(scenario, actor, scenario.seer_id)

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.INVALID_TARGET
    assert scenario.core.state.revision == revision


def test_non_seer_cannot_inspect(core_at_seer, actor):
    scenario, target = core_at_seer
    non_seer_id = next(seat_id for seat_id in scenario.good_ids if seat_id != scenario.seer_id)

    result = scenario.core.submit(
        envelope_for(
            scenario.core,
            non_seer_id,
            SeerInspectCommand(target_seat_id=target),
        ),
        actor(non_seer_id),
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
