from tests.factories import (
    Scenario,
    envelope_for,
    living_ids,
    tick_to_witch_deadline,
)
from werewolf_dm.domain.contracts import (
    CommandErrorCode,
    PassSpeechCommand,
    WitchSkipCommand,
    WitchUseAntidoteCommand,
    WitchUsePoisonCommand,
)
from werewolf_dm.domain.enums import EventType, Phase


def _submit_witch_action(
    scenario: Scenario,
    actor,
    payload: WitchUseAntidoteCommand | WitchUsePoisonCommand | WitchSkipCommand,
):
    return scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, payload),
        actor(scenario.witch_id),
    )


def _latest_players_died(core):
    return next(
        event for event in reversed(core.events) if event.event_type is EventType.PLAYERS_DIED
    )


def test_st_007_antidote_cancels_wolf_kill(core_at_witch, actor):
    scenario, target = core_at_witch
    before = living_ids(scenario.core)

    result = _submit_witch_action(scenario, actor, WitchUseAntidoteCommand())

    assert result.accepted is True
    assert target in living_ids(scenario.core)
    assert set(living_ids(scenario.core)) == set(before)
    assert scenario.core.state.potions.antidote_available is False
    assert scenario.core.state.phase is Phase.DAY_DISCUSSION
    assert scenario.core.state.night.deaths == ()
    assert tuple(_latest_players_died(scenario.core).fact_payload["seat_ids"]) == ()


def test_st_008_poison_adds_independent_death(core_at_witch, actor):
    scenario, wolf_target = core_at_witch
    poison_target = scenario.wolf_ids[0]

    result = _submit_witch_action(
        scenario,
        actor,
        WitchUsePoisonCommand(target_seat_id=poison_target),
    )

    assert result.accepted is True
    assert wolf_target not in living_ids(scenario.core)
    assert poison_target not in living_ids(scenario.core)
    assert set(scenario.core.state.night.deaths) == {wolf_target, poison_target}
    assert scenario.core.state.potions.poison_available is False
    assert set(_latest_players_died(scenario.core).fact_payload["seat_ids"]) == {
        wolf_target,
        poison_target,
    }


def test_st_009_two_potions_same_night_rejected(core_at_witch, actor):
    scenario, _ = core_at_witch

    first = _submit_witch_action(scenario, actor, WitchUseAntidoteCommand())
    revision = scenario.core.state.revision
    second = _submit_witch_action(
        scenario,
        actor,
        WitchUsePoisonCommand(target_seat_id=scenario.wolf_ids[0]),
    )

    assert first.accepted is True
    assert second.accepted is False
    assert second.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert scenario.core.state.revision == revision


def test_st_010_repeated_antidote_rejected(core_after_antidote_used, actor):
    scenario = core_after_antidote_used
    revision = scenario.core.state.revision

    result = _submit_witch_action(scenario, actor, WitchUseAntidoteCommand())

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.POTION_ALREADY_USED
    assert scenario.core.state.revision == revision
    assert scenario.core.state.potions.antidote_available is False


def test_st_011_repeated_poison_rejected(core_after_poison_used, actor):
    scenario = core_after_poison_used
    revision = scenario.core.state.revision

    result = _submit_witch_action(
        scenario,
        actor,
        WitchUsePoisonCommand(
            target_seat_id=next(
                player.seat_id
                for player in scenario.core.state.players
                if player.alive and player.seat_id != scenario.witch_id
            )
        ),
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.POTION_ALREADY_USED
    assert scenario.core.state.revision == revision
    assert scenario.core.state.potions.poison_available is False


def test_st_012_dead_player_action_rejected(core_dead_witch, actor):
    scenario = core_dead_witch
    revision = scenario.core.state.revision

    result = scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, PassSpeechCommand()),
        actor(scenario.witch_id),
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.PLAYER_DEAD
    assert scenario.core.state.revision == revision


def test_st_018_living_witch_timeout_uses_no_potion(core_at_witch):
    scenario, target = core_at_witch

    tick_to_witch_deadline(scenario.core)

    assert scenario.core.state.phase is Phase.DAY_DISCUSSION
    assert scenario.core.state.night.action == "SKIP"
    assert target not in living_ids(scenario.core)
    assert scenario.core.state.potions.antidote_available is True
    assert scenario.core.state.potions.poison_available is True
    assert any(
        event.event_type is EventType.TIMEOUT_APPLIED
        and event.fact_payload["timeout_reason"] == "WITCH"
        for event in scenario.core.events
    )


def test_st_023_witch_is_kill_target_disables_antidote(core_with_witch_as_target, actor):
    scenario = core_with_witch_as_target
    rejected = _submit_witch_action(scenario, actor, WitchUseAntidoteCommand())

    assert rejected.error_code is CommandErrorCode.WITCH_SELF_RESCUE_FORBIDDEN

    poison_target = scenario.wolf_ids[0]
    accepted = _submit_witch_action(
        scenario,
        actor,
        WitchUsePoisonCommand(target_seat_id=poison_target),
    )

    assert accepted.accepted is True
    assert scenario.core.state.phase is Phase.DAY_DISCUSSION
    assert poison_target not in living_ids(scenario.core)
    assert scenario.witch_id not in living_ids(scenario.core)


def test_st_024_witch_cannot_self_save_and_remains_dead(core_with_witch_as_target, actor):
    scenario = core_with_witch_as_target
    rejected = _submit_witch_action(scenario, actor, WitchUseAntidoteCommand())
    assert rejected.error_code is CommandErrorCode.WITCH_SELF_RESCUE_FORBIDDEN

    accepted = _submit_witch_action(scenario, actor, WitchSkipCommand())

    assert accepted.accepted is True
    assert scenario.core.state.players[scenario.witch_id - 1].alive is False
    assert scenario.core.state.potions.antidote_available is True
    assert scenario.core.state.potions.poison_available is True


def test_seer_record_survives_witch_skip_and_night_resolve(core_at_witch, actor):
    scenario, _ = core_at_witch
    checks_before = scenario.core.state.seer_checks
    assert len(checks_before) == 1

    result = _submit_witch_action(scenario, actor, WitchSkipCommand())

    assert result.accepted is True
    assert scenario.core.state.phase is Phase.DAY_DISCUSSION
    assert scenario.core.state.seer_checks == checks_before
