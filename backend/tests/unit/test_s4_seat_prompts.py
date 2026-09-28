from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.factories import (
    Scenario,
    _advance_past_day,
    core_at_wolf,
    envelope_for,
)
from werewolf_dm.application.core import GameCore
from werewolf_dm.domain.contracts import (
    DomainEvent,
    SeerInspectCommand,
    WolfNominateKillCommand,
)
from werewolf_dm.domain.enums import EventType, Phase, Role
from werewolf_dm.domain.model import OutboxItem


def _latest_phase_event(core: GameCore, phase: Phase) -> DomainEvent:
    return next(
        event
        for event in reversed(core.events)
        if event.event_type is EventType.PHASE_CHANGED
        and event.fact_payload["next_phase"] == phase.value
    )


def _phase_prompt_items(core: GameCore, phase: Phase) -> tuple[OutboxItem, ...]:
    event = _latest_phase_event(core, phase)
    return tuple(
        item
        for item in core.state.outbox
        if item.kind == "dm.message"
        and item.event_id == event.event_id
        and getattr(item, "audience_seat_id", None) is not None
    )


def _living_role_seats(core: GameCore, role: Role) -> tuple[int, ...]:
    return tuple(
        player.seat_id for player in core.state.players if player.alive and player.role is role
    )


def _submit(
    scenario: Scenario,
    actor,
    seat_id: int,
    command: object,
) -> None:
    result = scenario.core.submit(
        envelope_for(scenario.core, seat_id, command),
        actor(seat_id),
    )
    assert result.accepted is True


def test_night_wolf_transition_enqueues_one_prompt_per_living_wolf() -> None:
    scenario = core_at_wolf()
    wolf_ids = _living_role_seats(scenario.core, Role.WEREWOLF)

    prompts = _phase_prompt_items(scenario.core, Phase.NIGHT_WOLF)

    assert tuple(item.audience_seat_id for item in prompts) == wolf_ids


def test_seat_prompt_outbox_items_share_phase_event_and_revision() -> None:
    scenario = core_at_wolf()
    event = _latest_phase_event(scenario.core, Phase.NIGHT_WOLF)

    prompts = _phase_prompt_items(scenario.core, Phase.NIGHT_WOLF)
    prompt_seqs: Sequence[int] = tuple(item.seq for item in prompts)

    assert len(prompts) == len(_living_role_seats(scenario.core, Role.WEREWOLF))
    assert all(item.event_id == event.event_id for item in prompts)
    assert all(item.revision == event.revision for item in prompts)
    assert prompt_seqs == tuple(sorted(set(prompt_seqs)))


@pytest.mark.parametrize("kind", ["view.updated", "game.ended"])
def test_audience_seat_id_requires_dm_message_kind(kind: str) -> None:
    with pytest.raises(ValidationError, match="audience_seat_id"):
        OutboxItem(
            seq=1,
            kind=kind,
            event_id=uuid4(),
            revision=0,
            audience_seat_id=1,
        )


def test_dead_seer_does_not_receive_phase_prompt(
    core_with_one_living_wolf_and_dead_seer,
    actor,
) -> None:
    scenario = core_with_one_living_wolf_and_dead_seer
    target = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.alive and player.role is not Role.WEREWOLF
    )
    living_wolf = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.alive and player.role is Role.WEREWOLF
    )

    _submit(
        scenario,
        actor,
        living_wolf,
        WolfNominateKillCommand(target_seat_id=target),
    )

    assert scenario.core.state.phase is Phase.NIGHT_WITCH
    assert _phase_prompt_items(scenario.core, Phase.NIGHT_SEER) == ()
    assert tuple(
        item.audience_seat_id for item in _phase_prompt_items(scenario.core, Phase.NIGHT_WITCH)
    ) == _living_role_seats(scenario.core, Role.WITCH)


def test_dead_witch_does_not_receive_phase_prompt(
    core_dead_witch,
    actor,
) -> None:
    scenario = core_dead_witch
    _advance_past_day(scenario.core)
    target = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.alive and player.role is not Role.WEREWOLF
    )
    for wolf_id in _living_role_seats(scenario.core, Role.WEREWOLF):
        _submit(
            scenario,
            actor,
            wolf_id,
            WolfNominateKillCommand(target_seat_id=target),
        )

    seer_id = _living_role_seats(scenario.core, Role.SEER)[0]
    seer_target = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.alive and player.seat_id != seer_id
    )
    _submit(scenario, actor, seer_id, SeerInspectCommand(target_seat_id=seer_target))

    assert tuple(
        item.audience_seat_id for item in _phase_prompt_items(scenario.core, Phase.NIGHT_SEER)
    ) == _living_role_seats(scenario.core, Role.SEER)
    assert _phase_prompt_items(scenario.core, Phase.NIGHT_WITCH) == ()
