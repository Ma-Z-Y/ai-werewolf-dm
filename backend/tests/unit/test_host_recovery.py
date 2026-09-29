from datetime import timedelta
from uuid import uuid4

import pytest

from tests.factories import (
    core as make_core,
)
from tests.factories import (
    core_at_day_vote,
    core_at_role_reveal,
    core_at_witch,
    core_at_wolf,
    host_actor,
    make_envelope,
    seat_actor,
)
from werewolf_dm.application.core import DomainSeqAllocator, GameCore
from werewolf_dm.domain import state_machine
from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    CommandErrorCode,
    HostPatch,
    HostPatchCommand,
    HostPauseCommand,
    SetAlivePatch,
    SetPhasePatch,
    SetPotionPatch,
    SetRolePatch,
    SetSeerChecksPatch,
    SetVotePatch,
    VoteCommand,
)
from werewolf_dm.domain.enums import EventType, Faction, Phase, Role
from werewolf_dm.domain.model import NightState, PrivateFact, SeerCheckRecord
from werewolf_dm.domain.state_machine import ApplyOutcome, apply_command, winner_for
from werewolf_dm.domain.visibility import project_public_view, project_seat_view


def _pause(core: GameCore) -> GameCore:
    actor = host_actor()
    result = core.submit(
        make_envelope(
            actor,
            HostPauseCommand(reason="host recovery test"),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )
    assert result.accepted is True
    assert core.state.paused is True
    return core


def _patch_outcome(
    core: GameCore,
    patch: HostPatch,
    *,
    actor: AuthenticatedActor | None = None,
) -> ApplyOutcome:
    authenticated = host_actor() if actor is None else actor
    envelope = make_envelope(
        authenticated,
        HostPatchCommand(patch=patch),
        expected_revision=core.state.revision,
        now=core.clock(),
    )
    return apply_command(core.state, envelope, authenticated, core.clock())


def _living_other(core: GameCore, seat_id: int) -> int:
    return next(
        player.seat_id
        for player in core.state.players
        if player.alive and player.seat_id != seat_id
    )


def fact_payload(fact: PrivateFact) -> dict[str, object]:
    return {key: value for key, value in fact.payload.items()}


def test_apply_host_patch_entrypoint_exists() -> None:
    assert callable(getattr(state_machine, "apply_host_patch", None))


def test_host_patch_requires_paused_state() -> None:
    core = core_at_role_reveal()
    before = core.state

    outcome = _patch_outcome(
        core,
        SetPotionPatch(antidote_available=False, poison_available=False),
    )

    assert outcome.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert outcome.next_state == before
    assert outcome.events == ()


def test_host_patch_requires_host_actor() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state

    outcome = _patch_outcome(
        core,
        SetPotionPatch(antidote_available=False, poison_available=False),
        actor=seat_actor(1),
    )

    assert outcome.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_alive_patch_changes_only_target_and_increments_revision() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state
    target = before.players[0]

    outcome = _patch_outcome(
        core,
        SetAlivePatch(seat_id=target.seat_id, alive=not target.alive),
    )

    assert outcome.error_code is None
    assert outcome.next_state.revision == before.revision + 1
    assert tuple(event.event_type for event in outcome.events) == (
        EventType.HOST_CORRECTION_APPLIED,
        EventType.HOST_COMPENSATION_APPLIED,
    )
    expected_players = tuple(
        player.model_copy(update={"alive": not player.alive})
        if player.seat_id == target.seat_id
        else player
        for player in before.players
    )
    assert outcome.next_state.players == expected_players
    assert outcome.next_state.outbox == before.outbox
    assert (
        outcome.next_state.model_copy(
            update={
                "revision": before.revision,
                "players": before.players,
                "event_count": before.event_count,
                "event_log_digest": before.event_log_digest,
            }
        )
        == before
    )


def test_invalid_patch_is_rejected_without_state_mutation() -> None:
    core = _pause(make_core())
    before = core.state

    outcome = _patch_outcome(
        core,
        SetRolePatch(seat_id=1, role=Role.WEREWOLF),
    )

    assert outcome.error_code is CommandErrorCode.INVALID_TARGET
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_role_patch_swaps_roles_without_changing_rulepack_counts() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state
    target = before.players[0]
    assert target.role is not None
    desired_role = next(role for role in Role if role is not target.role)
    other = next(
        player
        for player in before.players
        if player.seat_id != target.seat_id and player.role is desired_role
    )

    outcome = _patch_outcome(
        core,
        SetRolePatch(seat_id=target.seat_id, role=desired_role),
    )

    assert outcome.error_code is None
    updated_target = next(
        player for player in outcome.next_state.players if player.seat_id == target.seat_id
    )
    updated_other = next(
        player for player in outcome.next_state.players if player.seat_id == other.seat_id
    )
    assert updated_target.role is desired_role
    assert updated_other.role is target.role
    assert sorted(player.role for player in outcome.next_state.players) == sorted(
        player.role for player in before.players
    )
    assert outcome.next_state.revision == before.revision + 1


def test_set_phase_patch_cannot_jump_to_future_phase() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state

    outcome = _patch_outcome(core, SetPhasePatch(phase=Phase.NIGHT_WOLF))

    assert outcome.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_role_rejects_terminal_game() -> None:
    core = core_at_role_reveal()
    terminal = core.state.model_copy(
        update={
            "phase": Phase.GAME_END,
            "winner": Faction.GOOD,
            "paused": True,
            "paused_at": core.clock(),
        }
    )
    target = terminal.players[0]
    assert target.role is not None

    actor = host_actor()
    envelope = make_envelope(
        actor,
        HostPatchCommand(patch=SetRolePatch(seat_id=target.seat_id, role=target.role)),
        expected_revision=terminal.revision,
        now=core.clock(),
    )
    outcome = apply_command(terminal, envelope, actor, core.clock())

    assert outcome.error_code is CommandErrorCode.GAME_ENDED
    assert outcome.next_state == terminal
    assert outcome.events == ()


def test_set_potion_updates_only_potion_state() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state
    history_before = core.events
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetPotionPatch(
                    antidote_available=False,
                    poison_available=False,
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    assert core.state.revision == before.revision + 1
    assert core.state.potions.antidote_available is False
    assert core.state.potions.poison_available is False
    assert (
        core.state.model_copy(
            update={
                "revision": before.revision,
                "potions": before.potions,
                "event_count": before.event_count,
                "event_log_digest": before.event_log_digest,
            }
        )
        == before
    )
    assert core.events[: len(history_before)] == history_before


def test_set_vote_requires_matching_open_round_and_legal_target() -> None:
    core = _pause(core_at_day_vote())
    assert core.state.vote_round is not None
    round_ = core.state.vote_round
    voter_seat_id = round_.eligible_voter_ids[0]
    target_seat_id = _living_other(core, voter_seat_id)

    wrong_round = _patch_outcome(
        core,
        SetVotePatch(
            voter_seat_id=voter_seat_id,
            round_id=uuid4(),
            target_seat_id=target_seat_id,
        ),
    )
    assert wrong_round.error_code is not None
    assert wrong_round.next_state == core.state

    actor = host_actor()
    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetVotePatch(
                    voter_seat_id=voter_seat_id,
                    round_id=round_.round_id,
                    target_seat_id=target_seat_id,
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    assert core.state.vote_round is not None
    vote = next(vote for vote in core.state.vote_round.votes if vote.voter_seat_id == voter_seat_id)
    assert vote.target_seat_id == target_seat_id


def test_set_vote_rejects_self_vote_unconditionally() -> None:
    core = _pause(core_at_day_vote())
    assert core.state.vote_round is not None
    round_ = core.state.vote_round
    voter_seat_id = round_.eligible_voter_ids[0]
    before = core.state

    outcome = _patch_outcome(
        core,
        SetVotePatch(
            voter_seat_id=voter_seat_id,
            round_id=round_.round_id,
            target_seat_id=voter_seat_id,
        ),
    )

    assert outcome.error_code is CommandErrorCode.INVALID_TARGET
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_seer_checks_accepts_canonical_history() -> None:
    core = _pause(core_at_day_vote())
    before = core.state
    seer = next(player for player in before.players if player.role is Role.SEER)

    outcome = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=seer.seat_id,
            checks=list(before.seer_checks),
        ),
    )

    assert outcome.error_code is None
    assert outcome.next_state.seer_checks == before.seer_checks
    assert outcome.next_state.revision == before.revision + 1


def test_set_seer_checks_rejects_non_seer_duplicate_and_future_history() -> None:
    core = _pause(core_at_day_vote())
    before = core.state
    seer = next(player for player in before.players if player.role is Role.SEER)
    non_seer = next(player for player in before.players if player.role is not Role.SEER)
    assert before.seer_checks
    existing = before.seer_checks[0]

    wrong_seat = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=non_seer.seat_id,
            checks=list(before.seer_checks),
        ),
    )
    duplicate = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=seer.seat_id,
            checks=[existing, existing],
        ),
    )
    future = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=seer.seat_id,
            checks=[
                SeerCheckRecord(
                    day=before.day + 1,
                    target_seat_id=existing.target_seat_id,
                    faction=existing.faction,
                )
            ],
        ),
    )

    for outcome in (wrong_seat, duplicate, future):
        assert outcome.error_code is not None
        assert outcome.next_state == before
        assert outcome.events == ()


def test_set_seer_checks_rebuilds_seat_projection_history() -> None:
    core = _pause(core_at_day_vote())
    before = core.state
    seer = next(player for player in before.players if player.role is Role.SEER)
    previous = before.seer_checks[0]
    target = next(
        player
        for player in before.players
        if player.alive and player.role is not None and player.seat_id != seer.seat_id
    )
    corrected = SeerCheckRecord(
        day=before.day,
        target_seat_id=target.seat_id,
        faction=Faction.WEREWOLF if target.role is Role.WEREWOLF else Faction.GOOD,
    )
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetSeerChecksPatch(
                    seer_seat_id=seer.seat_id,
                    checks=[corrected],
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    view = project_seat_view(core.state, seer.seat_id, seat_actor(seer.seat_id))
    facts = tuple(fact for fact in view.private_facts if fact.fact_type == "SEER_CHECK")
    assert len(facts) == 1
    assert fact_payload(facts[0]) == {
        "day": corrected.day,
        "target_seat_id": corrected.target_seat_id,
        "faction": corrected.faction.value,
    }
    assert fact_payload(facts[0])["target_seat_id"] != previous.target_seat_id
    assert facts[0].revision == core.state.revision


def test_set_phase_night_wolf_rebuilds_current_wolf_decision_facts() -> None:
    core = _pause(core_at_wolf().core)
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(patch=SetPhasePatch(phase=Phase.NIGHT_WOLF)),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    wolf_ids = tuple(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.WEREWOLF
    )
    for wolf_id in wolf_ids:
        view = project_seat_view(core.state, wolf_id, seat_actor(wolf_id))
        facts = tuple(fact for fact in view.private_facts if fact.fact_type == "WOLF_DECISION")
        assert len(facts) == 1
        assert fact_payload(facts[0]) == {
            "nominations": (),
            "target_seat_id": None,
            "locked": False,
        }
        assert facts[0].revision == core.state.revision


def test_set_phase_night_witch_rebuilds_current_kill_target_fact() -> None:
    scenario, previous_target = core_at_witch()
    core = _pause(scenario.core)
    current_target = next(
        player.seat_id
        for player in core.state.players
        if player.alive
        and player.seat_id != scenario.witch_id
        and player.seat_id != previous_target
    )
    state = core.state.model_copy(
        update={
            "wolf_decision": core.state.wolf_decision.model_copy(
                update={"target_seat_id": current_target}
            )
        }
    )
    actor = host_actor()
    envelope = make_envelope(
        actor,
        HostPatchCommand(patch=SetPhasePatch(phase=Phase.NIGHT_WITCH)),
        expected_revision=state.revision,
        now=core.clock(),
    )

    outcome = apply_command(state, envelope, actor, core.clock())

    assert outcome.error_code is None
    view = project_seat_view(
        outcome.next_state,
        scenario.witch_id,
        seat_actor(scenario.witch_id),
    )
    facts = tuple(fact for fact in view.private_facts if fact.fact_type == "WITCH_KILL_TARGET")
    assert len(facts) == 1
    assert fact_payload(facts[0]) == {"target_seat_id": current_target}
    assert facts[0].revision == outcome.next_state.revision


def test_set_alive_rejects_candidate_that_creates_winner_condition() -> None:
    core = _pause(core_at_day_vote())
    good_seen = 0
    players = []
    for player in core.state.players:
        if player.role is Role.WEREWOLF:
            players.append(player.model_copy(update={"alive": True}))
            continue
        good_seen += 1
        players.append(player.model_copy(update={"alive": good_seen <= 3}))
    state = core.state.model_copy(
        update={
            "phase": Phase.DAY_DISCUSSION,
            "players": tuple(players),
            "discussion": None,
            "vote_round": None,
            "deadline_at": None,
        }
    )
    target = next(
        player for player in state.players if player.alive and player.role is not Role.WEREWOLF
    )
    assert winner_for(state) is None
    actor = host_actor()
    envelope = make_envelope(
        actor,
        HostPatchCommand(patch=SetAlivePatch(seat_id=target.seat_id, alive=False)),
        expected_revision=state.revision,
        now=core.clock(),
    )

    outcome = apply_command(state, envelope, actor, core.clock())

    assert outcome.error_code is CommandErrorCode.INVALID_TARGET
    assert outcome.next_state == state
    assert outcome.events == ()


def test_host_patch_authorization_rejection_is_not_dedupe_cached() -> None:
    core = _pause(core_at_role_reveal())
    actor = seat_actor(1)
    before_cache = dict(core.command_dedupe_cache)
    envelope = make_envelope(
        actor,
        HostPatchCommand(
            patch=SetPotionPatch(
                antidote_available=False,
                poison_available=False,
            )
        ),
        expected_revision=core.state.revision,
        now=core.clock(),
    )

    mutation = core.stage_submit(
        envelope,
        actor,
        next_domain_seq=DomainSeqAllocator(1),
    )

    assert mutation.command_result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
    assert mutation.dedupe_key is not None
    assert mutation.cache_result is False
    core.commit(mutation)
    assert core.command_dedupe_cache == before_cache


def test_set_phase_resets_discussion_vote_and_night_substate_for_target_phase() -> None:
    core = core_at_day_vote()
    assert core.state.vote_round is not None
    round_ = core.state.vote_round
    voter_seat_id = round_.eligible_voter_ids[0]
    target_seat_id = _living_other(core, voter_seat_id)
    voter = seat_actor(voter_seat_id)
    vote_result = core.submit(
        make_envelope(
            voter,
            VoteCommand(target_seat_id=target_seat_id),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        voter,
    )
    assert vote_result.accepted is True
    assert core.state.vote_round is not None
    assert core.state.vote_round.votes
    _pause(core)
    before_round_id = core.state.vote_round.round_id
    now = core.clock()

    outcome = _patch_outcome(core, SetPhasePatch(phase=Phase.DAY_VOTE))

    assert outcome.error_code is None
    assert outcome.next_state.phase is Phase.DAY_VOTE
    assert outcome.next_state.discussion is None
    assert outcome.next_state.night == NightState()
    assert outcome.next_state.vote_round is not None
    assert outcome.next_state.vote_round.round_id != before_round_id
    assert outcome.next_state.vote_round.votes == ()
    assert outcome.next_state.vote_round.opened_at == now
    assert outcome.next_state.vote_round.deadline_at == now + timedelta(seconds=30)
    assert outcome.next_state.deadline_at == now + timedelta(seconds=30)


def test_host_patch_events_never_enter_public_or_seat_projections() -> None:
    core = _pause(core_at_role_reveal())
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetPotionPatch(
                    antidote_available=False,
                    poison_available=True,
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    assert all(event.visibility.scope == "host" for event in core.events[-2:])
    public = project_public_view(core.state).model_dump_json()
    seat = project_seat_view(core.state, 1, seat_actor(1)).model_dump_json()
    for serialized in (public, seat):
        for forbidden in (
            "HOST_COMPENSATION_APPLIED",
            "snapshot_id",
            "diff",
            "fact_payload",
            "token",
        ):
            assert forbidden not in serialized


def test_domain_seq_allocator_reserves_contiguous_ranges_and_rolls_back() -> None:
    allocator = DomainSeqAllocator(7)

    first = allocator.reserve(2)
    second = allocator.reserve(3)

    assert (first.start, first.count) == (7, 2)
    assert (second.start, second.count) == (9, 3)
    assert allocator.current() == 12

    allocator.rollback(second)
    assert allocator.current() == 9
    allocator.rollback(first)
    assert allocator.current() == 7


def test_domain_seq_allocator_commit_closes_reservation() -> None:
    allocator = DomainSeqAllocator(4)
    reservation = allocator.reserve(2)

    allocator.commit(reservation)

    assert allocator.current() == 6
    with pytest.raises(ValueError, match="not open"):
        allocator.rollback(reservation)
