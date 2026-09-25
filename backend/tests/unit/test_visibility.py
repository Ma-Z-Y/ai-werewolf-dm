from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from tests.factories import (
    envelope_for,
    submit_votes,
    tick_to_seer_deadline,
    tick_to_wolf_deadline,
)
from werewolf_dm.domain.contracts import (
    AbstainCommand,
    AuthenticatedActor,
    CommandEnvelope,
    CommandErrorCode,
    CommandType,
    DomainEvent,
    JoinRoomCommand,
    PassSpeechCommand,
    PublicVisibility,
    RoomJoinedPayload,
    SeatVisibility,
    VoteCommand,
    WitchSkipCommand,
    WitchUseAntidoteCommand,
    build_event,
)
from werewolf_dm.domain.enums import EventType, Phase, Role
from werewolf_dm.domain.visibility import (
    ProjectionAccessError,
    legal_actions,
    project_host_audit,
    project_player_replay,
    project_public_view,
    project_seat_view,
)


def _advance_one_day(core) -> None:
    while core.state.phase is Phase.DAY_DISCUSSION:
        discussion = core.state.discussion
        assert discussion is not None
        assert discussion.current_seat_id is not None
        result = core.submit(
            envelope_for(core, discussion.current_seat_id, PassSpeechCommand()),
            AuthenticatedActor(
                actor_type="seat",
                seat_id=discussion.current_seat_id,
                room_id=core.state.room_id,
            ),
        )
        assert result.accepted is True

    assert core.state.phase is Phase.DAY_VOTE
    assert core.state.vote_round is not None
    for voter_seat_id in core.state.vote_round.eligible_voter_ids:
        result = core.submit(
            envelope_for(core, voter_seat_id, AbstainCommand()),
            AuthenticatedActor(
                actor_type="seat",
                seat_id=voter_seat_id,
                room_id=core.state.room_id,
            ),
        )
        assert result.accepted is True

    assert core.state.phase is Phase.NIGHT_WOLF


def _seat_view(state, seat_id: int):
    return project_seat_view(
        state,
        seat_id,
        AuthenticatedActor(actor_type="seat", seat_id=seat_id, room_id=state.room_id),
    )


def test_vis_001_villager_public_view_has_no_role_or_secret_fields(core_at_night):
    public = project_public_view(core_at_night.state)
    payload = public.model_dump(mode="json")

    assert "role" not in payload
    assert "potions" not in payload
    assert "seer_checks" not in payload
    assert "wolf_decision" not in payload
    assert "private_facts" not in payload


def test_vis_002_seer_view_has_only_own_checks(core_with_two_seer_checks):
    scenario = core_with_two_seer_checks
    view = _seat_view(scenario.core.state, scenario.seer_id)

    assert len(view.private_facts) == 2
    assert all(fact.recipient_seat_id == scenario.seer_id for fact in view.private_facts)
    assert all(fact.fact_type == "SEER_CHECK" for fact in view.private_facts)


def test_vis_003_witch_view_has_potions_and_current_kill_target(core_at_witch):
    scenario, target = core_at_witch
    view = _seat_view(scenario.core.state, scenario.witch_id)
    facts = {fact.fact_type: fact for fact in view.private_facts}

    assert "WITCH_KILL_TARGET" in facts
    assert facts["WITCH_KILL_TARGET"].payload["target_seat_id"] == target
    assert "WITCH_POTIONS" in facts
    assert facts["WITCH_POTIONS"].payload == {
        "antidote_available": True,
        "poison_available": True,
    }


def test_vis_004_wolf_view_has_team_and_wolf_decision(core_at_wolf):
    view = _seat_view(core_at_wolf.core.state, core_at_wolf.wolf_ids[0])
    fact_types = {fact.fact_type for fact in view.private_facts}

    assert {"WOLF_TEAM", "WOLF_DECISION"} <= fact_types


def test_vis_005_dead_player_receives_no_new_private_facts(core_dead_witch):
    scenario = core_dead_witch
    before = _seat_view(scenario.core.state, scenario.witch_id).private_facts

    _advance_one_day(scenario.core)

    after = _seat_view(scenario.core.state, scenario.witch_id).private_facts
    assert after == before


def test_vis_011_player_cannot_export_host_audit(core_at_night, actor):
    with pytest.raises(ProjectionAccessError, match="HOST_AUDIT_FORBIDDEN"):
        project_host_audit(core_at_night.state, tuple(core_at_night.events), actor(1))


def test_vis_012_player_replay_contains_only_public_and_own_facts(
    core_with_two_seer_checks,
    actor,
):
    scenario = core_with_two_seer_checks
    replay_view = project_player_replay(
        scenario.core.state,
        tuple(scenario.core.events),
        actor(scenario.seer_id),
    )

    assert all(fact.recipient_seat_id == scenario.seer_id for fact in replay_view.private_facts)
    assert all(event.visibility.scope != "host" for event in replay_view.events)
    assert all(event.visibility.scope in {"public", "seat"} for event in replay_view.events)


def test_vis_013_privileged_trace_and_snapshot_payloads_require_host(
    core_game_end,
    actor,
    host,
):
    with pytest.raises(ProjectionAccessError, match="HOST_AUDIT_FORBIDDEN"):
        project_host_audit(core_game_end.state, tuple(core_game_end.events), actor(1))

    audit = project_host_audit(core_game_end.state, tuple(core_game_end.events), host())
    assert audit.room_id == core_game_end.state.room_id
    assert audit.raw_events == tuple(core_game_end.events)
    assert audit.dm_trace == ()
    assert audit.snapshots == ()


def test_vis_014_seat_actor_cannot_cross_boundary(core, actor, envelope):
    with pytest.raises(ProjectionAccessError, match="SEAT_VIEW_FORBIDDEN"):
        project_seat_view(core.state, 5, actor(3))

    result = core.submit(
        envelope(
            actor(3),
            JoinRoomCommand(seat_id=5, display_name="forged"),
            expected_revision=core.state.revision,
        ),
        actor(3),
    )
    assert result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED


def test_project_seat_view_requires_actor(core):
    with pytest.raises(TypeError):
        project_seat_view(core.state, 1)


def test_reconnect_rejects_actor_from_another_room(core_at_night):
    foreign_actor = AuthenticatedActor(
        actor_type="seat",
        seat_id=core_at_night.state.players[1].seat_id,
        room_id=UUID("00000000-0000-0000-0000-000000000099"),
    )

    with pytest.raises(ProjectionAccessError, match="SEAT_VIEW_FORBIDDEN"):
        core_at_night.reconnect(foreign_actor)


def test_vis_015_extra_actor_field_rejected():
    with pytest.raises(ValidationError):
        AuthenticatedActor.model_validate(
            {
                "actor_type": "seat",
                "seat_id": 3,
                "room_id": "00000000-0000-0000-0000-000000000001",
                "actor_seat": 5,
            }
        )


def test_vis_015_extra_command_envelope_field_rejected(actor):
    with pytest.raises(ValidationError):
        CommandEnvelope.model_validate(
            {
                "schema_version": "command.v1",
                "command_id": "00000000-0000-0000-0000-000000000010",
                "room_id": actor(1).room_id,
                "expected_revision": 0,
                "issued_at": "2026-01-01T00:00:00Z",
                "payload": {
                    "command_type": "JOIN_ROOM",
                    "seat_id": 1,
                    "display_name": "A",
                },
                "actor_seat": 5,
            }
        )


def test_task11_closed_vote_round_is_history_not_active(core_at_day_vote):
    core = core_at_day_vote
    target_seat_id = next(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.VILLAGER
    )
    assert core.state.vote_round is not None
    votes = {
        voter_seat_id: None if voter_seat_id == target_seat_id else target_seat_id
        for voter_seat_id in core.state.vote_round.eligible_voter_ids
    }
    submit_votes(core, votes)

    assert core.state.phase is Phase.NIGHT_WOLF
    assert core.state.vote_round is not None
    assert core.state.vote_round.closed is True
    public_view = project_public_view(core.state)
    seat_view = _seat_view(core.state, 1)
    public_payload = public_view.model_dump(mode="json")
    seat_payload = seat_view.model_dump(mode="json")
    seat_actions = {action.action for action in seat_view.legal_actions}

    assert public_view.vote_summary is None
    assert seat_view.vote_summary is None
    assert public_payload["vote_summary"] is None
    assert seat_payload["vote_summary"] is None
    assert "candidate_seat_ids" not in public_payload
    assert "candidate_seat_ids" not in seat_payload
    assert "tallies" not in public_payload
    assert "tallies" not in seat_payload
    assert "votes" not in public_payload
    assert "votes" not in seat_payload
    assert CommandType.VOTE not in seat_actions
    assert CommandType.ABSTAIN not in seat_actions


def test_legal_actions_follow_role_potions_and_phase(core_at_witch):
    scenario, _ = core_at_witch
    actions = legal_actions(scenario.core.state, scenario.witch_id)
    action_types = {action.action for action in actions}

    assert {
        CommandType.WITCH_USE_ANTIDOTE,
        CommandType.WITCH_USE_POISON,
        CommandType.WITCH_SKIP,
    } <= action_types
    assert len(_seat_view(scenario.core.state, scenario.witch_id).private_facts) >= 2


def test_witch_cannot_use_antidote_when_wolves_miss(core_at_wolf, actor):
    scenario = core_at_wolf
    tick_to_wolf_deadline(scenario.core)
    tick_to_seer_deadline(scenario.core)

    assert scenario.core.state.phase is Phase.NIGHT_WITCH
    assert scenario.core.state.wolf_decision.target_seat_id is None
    actions = legal_actions(scenario.core.state, scenario.witch_id)
    assert CommandType.WITCH_USE_ANTIDOTE not in {action.action for action in actions}

    revision = scenario.core.state.revision
    result = scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, WitchUseAntidoteCommand()),
        actor(scenario.witch_id),
    )

    assert result.error_code is CommandErrorCode.INVALID_TARGET
    assert scenario.core.state.revision == revision
    assert scenario.core.state.potions.antidote_available is True


def test_active_vote_replay_does_not_leak_individual_votes(core_at_day_vote):
    core = core_at_day_vote
    round_ = core.state.vote_round
    assert round_ is not None
    voter_seat_id = round_.eligible_voter_ids[0]
    target_seat_id = next(
        seat_id for seat_id in round_.eligible_voter_ids if seat_id != voter_seat_id
    )
    result = core.submit(
        envelope_for(core, voter_seat_id, VoteCommand(target_seat_id=target_seat_id)),
        AuthenticatedActor(
            actor_type="seat",
            seat_id=voter_seat_id,
            room_id=core.state.room_id,
        ),
    )
    assert result.accepted is True
    observer_seat_id = next(
        seat_id for seat_id in round_.eligible_voter_ids if seat_id not in {voter_seat_id}
    )
    replay_view = project_player_replay(
        core.state,
        tuple(core.events),
        AuthenticatedActor(
            actor_type="seat",
            seat_id=observer_seat_id,
            room_id=core.state.room_id,
        ),
    )

    assert all(
        event.event_type not in {EventType.VOTE_RECORDED, EventType.ABSTAIN_RECORDED}
        for event in replay_view.events
    )


def test_public_death_event_omits_cause_while_host_audit_keeps_it(
    core_at_witch,
    actor,
    host,
):
    scenario, _ = core_at_witch
    result = scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, WitchSkipCommand()),
        actor(scenario.witch_id),
    )
    assert result.accepted is True

    public_deaths = tuple(
        event
        for event in scenario.core.events
        if event.event_type is EventType.PLAYERS_DIED and event.visibility.scope == "public"
    )
    host_audit = project_host_audit(scenario.core.state, tuple(scenario.core.events), host())
    host_deaths = tuple(
        event
        for event in host_audit.raw_events
        if event.event_type is EventType.PLAYERS_DIED and event.visibility.scope == "host"
    )

    assert public_deaths
    assert all("cause" not in event.fact_payload for event in public_deaths)
    assert host_deaths
    assert all(event.fact_payload["cause"] in {"WOLF", "POISON", "MIXED"} for event in host_deaths)


def _foreign_room_event(core):
    return build_event(
        cause_id=UUID("00000000-0000-0000-0000-0000000000aa"),
        event_ordinal=1,
        room_id=UUID("00000000-0000-0000-0000-0000000000bb"),
        revision=1,
        event_type=EventType.ROOM_JOINED,
        visibility=PublicVisibility(),
        payload=RoomJoinedPayload(seat_id=1, display_name="Foreign"),
        created_at=core.clock(),
    )


def _future_revision_event(core):
    return build_event(
        cause_id=UUID("00000000-0000-0000-0000-0000000000cc"),
        event_ordinal=1,
        room_id=core.state.room_id,
        revision=core.state.revision + 1,
        event_type=EventType.ROOM_JOINED,
        visibility=PublicVisibility(),
        payload=RoomJoinedPayload(seat_id=1, display_name="Future"),
        created_at=core.clock(),
    )


def _revision_event(core, revision: int, ordinal: int):
    return build_event(
        cause_id=UUID(f"00000000-0000-0000-0000-{ordinal:012d}"),
        event_ordinal=ordinal,
        room_id=core.state.room_id,
        revision=revision,
        event_type=EventType.ROOM_JOINED,
        visibility=PublicVisibility(),
        payload=RoomJoinedPayload(seat_id=1, display_name=f"Probe{ordinal}"),
        created_at=core.clock(),
    )


def _assert_projection_event_mismatch(core, events, actor, host):
    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(core.state, events, actor(1))
    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_host_audit(core.state, events, host())


def test_player_replay_rejects_cross_room_and_future_events(core_at_night, actor):
    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(
            core_at_night.state,
            (_foreign_room_event(core_at_night),),
            actor(1),
        )
    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(
            core_at_night.state,
            (_future_revision_event(core_at_night),),
            actor(1),
        )


def test_host_audit_rejects_cross_room_and_future_events(core_at_night, host):
    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_host_audit(
            core_at_night.state,
            (_foreign_room_event(core_at_night),),
            host(),
        )
    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_host_audit(
            core_at_night.state,
            (_future_revision_event(core_at_night),),
            host(),
        )


def test_projections_reject_incomplete_event_logs(core_at_night, actor, host):
    core = core_at_night
    truncated_events = tuple(event for event in core.events if event.revision < core.state.revision)

    _assert_projection_event_mismatch(core, (), actor, host)
    _assert_projection_event_mismatch(core, truncated_events, actor, host)
    _assert_projection_event_mismatch(
        core,
        (_revision_event(core, 1, 201), _revision_event(core, 3, 202)),
        actor,
        host,
    )
    _assert_projection_event_mismatch(
        core,
        (_revision_event(core, 0, 203),),
        actor,
        host,
    )
    _assert_projection_event_mismatch(
        core,
        (*core.events, core.events[-1]),
        actor,
        host,
    )


def test_player_replay_revalidates_seat_visibility_binding(core_at_witch, actor):
    scenario, _ = core_at_witch
    result = scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, WitchSkipCommand()),
        actor(scenario.witch_id),
    )
    assert result.accepted is True

    witch_event = next(
        event
        for event in scenario.core.events
        if event.event_type is EventType.WITCH_ACTION_RECORDED
    )
    forged_event = DomainEvent.model_construct(
        **{
            **witch_event.model_dump(),
            "visibility": SeatVisibility(seat_id=1 if scenario.witch_id != 1 else 2),
        }
    )
    forged_events = tuple(
        forged_event if event.event_id == witch_event.event_id else event
        for event in scenario.core.events
    )

    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(
            scenario.core.state, forged_events, actor(forged_event.visibility.seat_id)
        )


@pytest.mark.parametrize("forged_witch_seat_id", [True, 1.0])
def test_player_replay_rejects_coerced_witch_payload_seat(
    core_at_witch,
    actor,
    forged_witch_seat_id,
):
    scenario, _ = core_at_witch
    result = scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, WitchSkipCommand()),
        actor(scenario.witch_id),
    )
    assert result.accepted is True

    witch_event = next(
        event
        for event in scenario.core.events
        if event.event_type is EventType.WITCH_ACTION_RECORDED
    )
    forged_event = DomainEvent.model_construct(
        **{
            **witch_event.model_dump(),
            "visibility": witch_event.visibility,
            "fact_payload": {
                **witch_event.fact_payload,
                "witch_seat_id": forged_witch_seat_id,
            },
        }
    )
    forged_events = tuple(
        forged_event if event.event_id == witch_event.event_id else event
        for event in scenario.core.events
    )

    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(
            scenario.core.state,
            forged_events,
            actor(scenario.witch_id),
        )


def test_player_replay_rejects_raw_dict_visibility(core_at_witch, actor):
    scenario, _ = core_at_witch
    result = scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, WitchSkipCommand()),
        actor(scenario.witch_id),
    )
    assert result.accepted is True

    witch_event = next(
        event
        for event in scenario.core.events
        if event.event_type is EventType.WITCH_ACTION_RECORDED
    )
    forged_event = DomainEvent.model_construct(
        **{
            **witch_event.model_dump(),
            "visibility": {
                "scope": "seat",
                "seat_id": scenario.witch_id,
            },
        }
    )
    forged_events = tuple(
        forged_event if event.event_id == witch_event.event_id else event
        for event in scenario.core.events
    )

    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(
            scenario.core.state,
            forged_events,
            actor(scenario.witch_id),
        )


def test_projections_reject_same_revision_tail_truncation(core_at_witch, actor, host):
    core = core_at_witch[0].core
    truncated = core.events[:-1]

    assert truncated
    assert truncated[-1].revision == core.state.revision
    _assert_projection_event_mismatch(core, truncated, actor, host)


def test_projections_reject_same_revision_event_substitution(core_at_witch, actor, host):
    core = core_at_witch[0].core
    original = core.events[-1]
    tampered = original.model_copy(
        update={"created_at": original.created_at + timedelta(seconds=1)}
    )
    substituted = (*core.events[:-1], tampered)

    _assert_projection_event_mismatch(core, substituted, actor, host)


def test_player_replay_rejects_synchronized_witch_visibility_forgery(core_at_witch, actor):
    scenario, _ = core_at_witch
    result = scenario.core.submit(
        envelope_for(scenario.core, scenario.witch_id, WitchSkipCommand()),
        actor(scenario.witch_id),
    )
    assert result.accepted is True

    original = next(
        event
        for event in scenario.core.events
        if event.event_type is EventType.WITCH_ACTION_RECORDED
    )
    victim = 1 if scenario.witch_id != 1 else 2
    forged = DomainEvent.model_construct(
        **{
            **original.model_dump(),
            "visibility": SeatVisibility(seat_id=victim),
            "fact_payload": {
                **original.fact_payload,
                "witch_seat_id": victim,
            },
        }
    )
    forged_events = tuple(
        forged if event.event_id == original.event_id else event for event in scenario.core.events
    )

    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(scenario.core.state, forged_events, actor(victim))


def test_player_replay_rejects_synchronized_role_assignment_forgery(core_at_night, actor):
    original = next(
        event for event in core_at_night.events if event.event_type is EventType.ROLE_ASSIGNED
    )
    original_seat_id = original.fact_payload["seat_id"]
    victim = next(seat_id for seat_id in range(1, 7) if seat_id != original_seat_id)
    forged = DomainEvent.model_construct(
        **{
            **original.model_dump(),
            "visibility": SeatVisibility(seat_id=victim),
            "fact_payload": {
                **original.fact_payload,
                "seat_id": victim,
            },
        }
    )
    forged_events = tuple(
        forged if event.event_id == original.event_id else event for event in core_at_night.events
    )

    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(core_at_night.state, forged_events, actor(victim))


def test_player_replay_rejects_synchronized_seer_check_forgery(
    core_with_two_seer_checks,
    actor,
):
    scenario = core_with_two_seer_checks
    original = next(
        event for event in scenario.core.events if event.event_type is EventType.SEER_CHECKED
    )
    victim = next(seat_id for seat_id in range(1, 7) if seat_id != scenario.seer_id)
    forged = DomainEvent.model_construct(
        **{
            **original.model_dump(),
            "visibility": SeatVisibility(seat_id=victim),
            "fact_payload": {
                **original.fact_payload,
                "seer_seat_id": victim,
            },
        }
    )
    forged_events = tuple(
        forged if event.event_id == original.event_id else event for event in scenario.core.events
    )

    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(scenario.core.state, forged_events, actor(victim))


def test_projections_reject_uuid_subclass_room_id(core_at_night):
    class EqualUUID(UUID):
        def __eq__(self, other):
            return True

        __hash__ = UUID.__hash__

    hostile_room = EqualUUID("00000000-0000-0000-0000-0000000000bb")
    forged_seat = AuthenticatedActor.model_construct(
        actor_type="seat",
        seat_id=1,
        room_id=hostile_room,
    )
    forged_host = AuthenticatedActor.model_construct(
        actor_type="host",
        seat_id=None,
        room_id=hostile_room,
    )

    with pytest.raises(ProjectionAccessError, match="SEAT_VIEW_FORBIDDEN"):
        project_seat_view(core_at_night.state, 1, forged_seat)
    with pytest.raises(ProjectionAccessError, match="PLAYER_REPLAY_FORBIDDEN"):
        project_player_replay(
            core_at_night.state,
            tuple(core_at_night.events),
            forged_seat,
        )
    with pytest.raises(ProjectionAccessError, match="HOST_AUDIT_FORBIDDEN"):
        project_host_audit(
            core_at_night.state,
            tuple(core_at_night.events),
            forged_host,
        )


def test_projections_reject_forged_initial_event_metadata(core_at_night, actor, host):
    forged_state = core_at_night.state.model_copy(
        update={
            "revision": 0,
            "event_count": 1,
            "event_log_digest": "f" * 64,
        }
    )

    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_player_replay(forged_state, (), actor(1))
    with pytest.raises(ProjectionAccessError, match="PROJECTION_EVENT_MISMATCH"):
        project_host_audit(forged_state, (), host())

    valid_state = forged_state.model_copy(
        update={
            "event_count": 0,
            "event_log_digest": "0" * 64,
        }
    )
    assert project_host_audit(valid_state, (), host()).raw_events == ()


@pytest.mark.parametrize("forged_seat_id", [True, 1.0])
def test_projections_reject_constructed_actor(core_at_night, forged_seat_id):
    forged = AuthenticatedActor.model_construct(
        actor_type="seat",
        seat_id=forged_seat_id,
        room_id=core_at_night.state.room_id,
    )

    with pytest.raises(ProjectionAccessError, match="SEAT_VIEW_FORBIDDEN"):
        project_seat_view(core_at_night.state, 1, forged)
    with pytest.raises(ProjectionAccessError, match="PLAYER_REPLAY_FORBIDDEN"):
        project_player_replay(core_at_night.state, tuple(core_at_night.events), forged)
