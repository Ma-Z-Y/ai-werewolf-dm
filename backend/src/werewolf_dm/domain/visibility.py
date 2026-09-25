from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue

from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    DomainEvent,
    FactionVisibility,
    HostVisibility,
    PublicVisibility,
    SeatVisibility,
)
from werewolf_dm.domain.enums import CommandType, EventType, Faction, Phase, Role
from werewolf_dm.domain.model import (
    GameState,
    Player,
    PrivateFact,
    PublicTimelineItem,
    StrictModel,
    VoteSummary,
)
from werewolf_dm.domain.replay import event_log_digest


class PublicView(StrictModel):
    schema_version: Literal["public-view.v1", "seat-view.v1"] = "public-view.v1"
    room_id: UUID
    revision: int = Field(ge=0)
    phase: Phase
    day: int = Field(ge=0)
    living_seats: list[int]
    public_timeline: list[PublicTimelineItem]
    vote_summary: VoteSummary | None
    deadline_at: datetime | None


class LegalAction(StrictModel):
    action: CommandType
    target_seat_ids: tuple[int, ...] = ()
    deadline_at: datetime | None = None


class SeatView(PublicView):
    schema_version: Literal["seat-view.v1"] = "seat-view.v1"
    seat_id: int = Field(ge=1, le=6)
    role: Role | None
    private_facts: tuple[PrivateFact, ...]
    legal_actions: tuple[LegalAction, ...]


class PlayerReplay(StrictModel):
    room_id: UUID
    revision: int = Field(ge=0)
    public_timeline: tuple[PublicTimelineItem, ...]
    private_facts: tuple[PrivateFact, ...]
    events: tuple[DomainEvent, ...]


class HostAuditExport(StrictModel):
    room_id: UUID
    revision: int = Field(ge=0)
    state: GameState
    raw_events: tuple[DomainEvent, ...]
    dm_trace: tuple[dict[str, JsonValue], ...] = ()
    snapshots: tuple[dict[str, JsonValue], ...] = ()


class ProjectionAccessError(PermissionError):
    pass


def _player_at(state: GameState, seat_id: int) -> Player | None:
    return next((player for player in state.players if player.seat_id == seat_id), None)


def _living_others(state: GameState, seat_id: int) -> tuple[int, ...]:
    return tuple(
        player.seat_id for player in state.players if player.alive and player.seat_id != seat_id
    )


def _active_vote_summary(state: GameState) -> VoteSummary | None:
    round_ = state.vote_round
    if (
        round_ is None
        or round_.closed
        or round_.phase is not state.phase
        or state.phase not in {Phase.DAY_VOTE, Phase.DAY_PK_VOTE}
    ):
        return None
    return VoteSummary(
        round_id=round_.round_id,
        tallies=(),
        abstention_count=0,
        closed=False,
    )


def _validate_event_log(
    state: GameState,
    events: tuple[DomainEvent, ...],
) -> tuple[DomainEvent, ...]:
    if state.revision == 0:
        if events or state.event_count != 0 or state.event_log_digest != event_log_digest(()):
            raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
        return ()
    if not events or events[0].revision != 1:
        raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")

    previous_revision = 1
    event_ids: set[UUID] = set()
    validated_events: list[DomainEvent] = []
    for event in events:
        if not isinstance(
            event.visibility,
            (PublicVisibility, HostVisibility, FactionVisibility, SeatVisibility),
        ):
            raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
        try:
            validated = DomainEvent.revalidate(event)
        except (AttributeError, TypeError, ValueError):
            raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH") from None
        if (
            validated.room_id != state.room_id
            or validated.revision < previous_revision
            or validated.revision > previous_revision + 1
            or validated.event_id in event_ids
        ):
            raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
        previous_revision = validated.revision
        event_ids.add(validated.event_id)
        validated_events.append(validated)
    if previous_revision != state.revision:
        raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
    if len(validated_events) != state.event_count:
        raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
    if event_log_digest(tuple(validated_events)) != state.event_log_digest:
        raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
    for event in validated_events:
        _validate_state_event_binding(state, event)
    return tuple(validated_events)


def _validate_state_event_binding(state: GameState, event: DomainEvent) -> None:
    if not isinstance(event.visibility, SeatVisibility):
        return
    players = {player.seat_id: player for player in state.players}
    actor = players.get(event.visibility.seat_id)
    if actor is None or actor.role is None:
        raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")

    if event.event_type is EventType.ROLE_ASSIGNED:
        if actor.role.value != event.fact_payload.get("role"):
            raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
    elif event.event_type is EventType.SEER_CHECKED:
        target_seat_id = event.fact_payload.get("target_seat_id")
        target = players.get(target_seat_id) if isinstance(target_seat_id, int) else None
        if actor.role is not Role.SEER or target is None or target.role is None:
            raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
        target_faction = Faction.WEREWOLF if target.role is Role.WEREWOLF else Faction.GOOD
        if event.fact_payload.get("target_faction") != target_faction.value:
            raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")
    elif event.event_type is EventType.WITCH_ACTION_RECORDED and actor.role is not Role.WITCH:
        raise ProjectionAccessError("PROJECTION_EVENT_MISMATCH")


def _legal_action(
    action: CommandType,
    *,
    target_seat_ids: tuple[int, ...] = (),
    deadline_at: datetime | None = None,
) -> LegalAction:
    return LegalAction(
        action=action,
        target_seat_ids=target_seat_ids,
        deadline_at=deadline_at,
    )


def legal_actions(state: GameState, seat_id: int) -> tuple[LegalAction, ...]:
    if type(seat_id) is not int or seat_id < 1 or seat_id > 6:
        raise ValueError("seat_id must be an exact integer between 1 and 6")
    state = GameState.revalidate(state)
    if state.paused or state.phase is Phase.GAME_END:
        return ()

    player = _player_at(state, seat_id)
    if player is None:
        if state.phase is Phase.LOBBY:
            return (_legal_action(CommandType.JOIN_ROOM, deadline_at=state.deadline_at),)
        return ()
    if not player.alive:
        return ()

    deadline_at = state.deadline_at
    if state.phase is Phase.LOBBY:
        return (_legal_action(CommandType.SET_READY, deadline_at=deadline_at),)
    if state.phase is Phase.ROLE_REVEAL:
        if player.role_confirmed:
            return ()
        return (_legal_action(CommandType.CONFIRM_ROLE, deadline_at=deadline_at),)
    if state.phase is Phase.NIGHT_WOLF:
        if player.role is not Role.WEREWOLF:
            return ()
        return (
            _legal_action(
                CommandType.WOLF_NOMINATE_KILL,
                target_seat_ids=tuple(
                    candidate.seat_id
                    for candidate in state.players
                    if candidate.alive and candidate.role is not Role.WEREWOLF
                ),
                deadline_at=deadline_at,
            ),
        )
    if state.phase is Phase.NIGHT_SEER:
        if player.role is not Role.SEER:
            return ()
        return (
            _legal_action(
                CommandType.SEER_INSPECT,
                target_seat_ids=_living_others(state, player.seat_id),
                deadline_at=deadline_at,
            ),
        )
    if state.phase is Phase.NIGHT_WITCH:
        if player.role is not Role.WITCH:
            return ()
        actions: list[LegalAction] = []
        if (
            state.potions.antidote_available
            and state.wolf_decision.target_seat_id is not None
            and state.wolf_decision.target_seat_id != player.seat_id
        ):
            actions.append(
                _legal_action(
                    CommandType.WITCH_USE_ANTIDOTE,
                    deadline_at=deadline_at,
                )
            )
        poison_targets = _living_others(state, player.seat_id)
        if state.potions.poison_available and poison_targets:
            actions.append(
                _legal_action(
                    CommandType.WITCH_USE_POISON,
                    target_seat_ids=poison_targets,
                    deadline_at=deadline_at,
                )
            )
        actions.append(_legal_action(CommandType.WITCH_SKIP, deadline_at=deadline_at))
        return tuple(actions)
    if state.phase in {Phase.DAY_DISCUSSION, Phase.DAY_PK_DISCUSSION}:
        discussion = state.discussion
        if discussion is None or discussion.current_seat_id != player.seat_id:
            return ()
        return (
            _legal_action(CommandType.SPEAK, deadline_at=discussion.deadline_at),
            _legal_action(CommandType.PASS_SPEECH, deadline_at=discussion.deadline_at),
        )
    if state.phase in {Phase.DAY_VOTE, Phase.DAY_PK_VOTE}:
        round_ = state.vote_round
        if (
            round_ is None
            or round_.closed
            or round_.phase is not state.phase
            or player.seat_id not in round_.eligible_voter_ids
        ):
            return ()
        targets = (
            round_.candidate_seat_ids
            if state.phase is Phase.DAY_PK_VOTE
            else _living_others(state, player.seat_id)
        )
        actions = []
        if targets:
            actions.append(
                _legal_action(
                    CommandType.VOTE,
                    target_seat_ids=targets,
                    deadline_at=round_.deadline_at,
                )
            )
        actions.append(
            _legal_action(
                CommandType.ABSTAIN,
                deadline_at=round_.deadline_at,
            )
        )
        return tuple(actions)
    return ()


def project_public_view(state: GameState) -> PublicView:
    state = GameState.revalidate(state)
    return PublicView(
        room_id=state.room_id,
        revision=state.revision,
        phase=state.phase,
        day=state.day,
        living_seats=[player.seat_id for player in state.players if player.alive],
        public_timeline=list(state.public_timeline),
        vote_summary=_active_vote_summary(state),
        deadline_at=state.deadline_at,
    )


def _validate_actor(
    actor: AuthenticatedActor,
    error_code: str,
) -> AuthenticatedActor:
    try:
        return AuthenticatedActor.revalidate(actor)
    except (AttributeError, TypeError, ValueError):
        raise ProjectionAccessError(error_code) from None


def project_seat_view(
    state: GameState,
    seat_id: int,
    actor: AuthenticatedActor,
) -> SeatView:
    if type(seat_id) is not int or seat_id < 1 or seat_id > 6:
        raise ProjectionAccessError("SEAT_VIEW_FORBIDDEN")
    state = GameState.revalidate(state)
    actor = _validate_actor(actor, "SEAT_VIEW_FORBIDDEN")
    if actor.actor_type != "seat" or actor.seat_id != seat_id or actor.room_id != state.room_id:
        raise ProjectionAccessError("SEAT_VIEW_FORBIDDEN")
    player = next(
        (player for player in state.players if player.seat_id == seat_id),
        None,
    )
    return SeatView(
        room_id=state.room_id,
        revision=state.revision,
        phase=state.phase,
        day=state.day,
        living_seats=[player.seat_id for player in state.players if player.alive],
        public_timeline=list(state.public_timeline),
        vote_summary=_active_vote_summary(state),
        deadline_at=state.deadline_at,
        seat_id=seat_id,
        role=player.role if player is not None else None,
        private_facts=tuple(
            fact for fact in state.private_facts if fact.recipient_seat_id == seat_id
        ),
        legal_actions=legal_actions(state, seat_id),
    )


def project_player_replay(
    state: GameState,
    events: tuple[DomainEvent, ...],
    actor: AuthenticatedActor,
) -> PlayerReplay:
    state = GameState.revalidate(state)
    actor = _validate_actor(actor, "PLAYER_REPLAY_FORBIDDEN")
    if actor.actor_type != "seat" or actor.seat_id is None or actor.room_id != state.room_id:
        raise ProjectionAccessError("PLAYER_REPLAY_FORBIDDEN")
    validated_events = _validate_event_log(state, events)

    visible_events = tuple(
        event
        for event in validated_events
        if event.visibility.scope == "public"
        or (event.visibility.scope == "seat" and event.visibility.seat_id == actor.seat_id)
    )
    return PlayerReplay(
        room_id=state.room_id,
        revision=state.revision,
        public_timeline=state.public_timeline,
        private_facts=tuple(
            fact for fact in state.private_facts if fact.recipient_seat_id == actor.seat_id
        ),
        events=visible_events,
    )


def project_host_audit(
    state: GameState,
    events: tuple[DomainEvent, ...],
    actor: AuthenticatedActor,
) -> HostAuditExport:
    state = GameState.revalidate(state)
    actor = _validate_actor(actor, "HOST_AUDIT_FORBIDDEN")
    if actor.actor_type != "host" or actor.room_id != state.room_id:
        raise ProjectionAccessError("HOST_AUDIT_FORBIDDEN")
    validated_events = _validate_event_log(state, events)
    return HostAuditExport(
        room_id=state.room_id,
        revision=state.revision,
        state=state,
        raw_events=validated_events,
    )
