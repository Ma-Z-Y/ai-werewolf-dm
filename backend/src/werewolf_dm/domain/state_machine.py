import random
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue

from werewolf_dm.domain.contracts import (
    EVENT_PAYLOAD_MODELS,
    AbstainCommand,
    AbstainRecordedPayload,
    AuthenticatedActor,
    CommandEnvelope,
    CommandErrorCode,
    ConfirmRoleCommand,
    DomainEvent,
    FactionVisibility,
    GameEndedPayload,
    HostForceTemplateCommand,
    HostPatchCommand,
    HostPauseCommand,
    HostPausedPayload,
    HostResumeCommand,
    HostResumedPayload,
    HostRewindToSnapshotCommand,
    HostVisibility,
    JoinRoomCommand,
    NoExilePayload,
    PassSpeechCommand,
    PhaseChangedPayload,
    PlayerExiledPayload,
    PlayersDiedPayload,
    PublicVisibility,
    ReadyChangedPayload,
    RoleAssignedPayload,
    RoleConfirmedPayload,
    RoomJoinedPayload,
    SeatVisibility,
    SeerCheckedPayload,
    SeerInspectCommand,
    SetReadyCommand,
    SpeakCommand,
    SpeechPassedPayload,
    SpeechRecordedPayload,
    SystemTimeout,
    TimeoutAppliedPayload,
    VoteCommand,
    VoteRecordedPayload,
    VoteRoundResolvedPayload,
    WitchActionPayload,
    WitchSkipCommand,
    WitchUseAntidoteCommand,
    WitchUsePoisonCommand,
    WolfNominateKillCommand,
    WolfNominationPayload,
    WolfTargetLockedPayload,
    build_event,
)
from werewolf_dm.domain.enums import EventType, Faction, Phase, Role
from werewolf_dm.domain.model import (
    ROLE_COUNTS,
    DiscussionState,
    GameState,
    NightState,
    OutboxItem,
    Player,
    PotionState,
    PrivateFact,
    PublicTimelineItem,
    RoleAssignment,
    SeatTally,
    SeerCheckRecord,
    StrictModel,
    Vote,
    VoteRound,
    WolfDecision,
    WolfNomination,
)
from werewolf_dm.domain.replay import advance_event_log_digest

ALLOWED_TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    Phase.LOBBY: frozenset({Phase.ROLE_REVEAL}),
    Phase.ROLE_REVEAL: frozenset({Phase.NIGHT_START}),
    Phase.NIGHT_START: frozenset({Phase.NIGHT_WOLF}),
    Phase.NIGHT_WOLF: frozenset({Phase.NIGHT_SEER}),
    Phase.NIGHT_SEER: frozenset({Phase.NIGHT_WITCH}),
    Phase.NIGHT_WITCH: frozenset({Phase.NIGHT_RESOLVE}),
    Phase.NIGHT_RESOLVE: frozenset({Phase.DAY_ANNOUNCE}),
    Phase.DAY_ANNOUNCE: frozenset({Phase.WIN_CHECK}),
    Phase.DAY_DISCUSSION: frozenset({Phase.DAY_VOTE}),
    Phase.DAY_VOTE: frozenset(
        {
            Phase.DAY_EXILE,
            Phase.DAY_PK_DISCUSSION,
            Phase.WIN_CHECK,
        }
    ),
    Phase.DAY_PK_DISCUSSION: frozenset({Phase.DAY_PK_VOTE}),
    Phase.DAY_PK_VOTE: frozenset({Phase.DAY_EXILE, Phase.WIN_CHECK}),
    Phase.DAY_EXILE: frozenset({Phase.WIN_CHECK}),
    Phase.WIN_CHECK: frozenset(
        {
            Phase.GAME_END,
            Phase.DAY_DISCUSSION,
            Phase.NIGHT_START,
        }
    ),
    Phase.GAME_END: frozenset(),
}

ROLE_REVEAL_SECONDS = 120
NIGHT_WOLF_SECONDS = 60
NIGHT_SEER_SECONDS = 30
NIGHT_WITCH_SECONDS = 30
DISCUSSION_SECONDS = 45
NORMAL_VOTE_SECONDS = 30
PK_DISCUSSION_SECONDS = 30
PK_VOTE_SECONDS = 20
DeathCause = Literal["WOLF", "POISON", "MIXED"]
WitchAction = Literal["ANTIDOTE", "POISON", "SKIP"]
PHASE_STATEMENT_LABELS: dict[Phase, str] = {
    Phase.LOBBY: "玩家准备",
    Phase.ROLE_REVEAL: "角色揭示",
    Phase.NIGHT_START: "夜晚开始",
    Phase.NIGHT_WOLF: "狼人行动",
    Phase.NIGHT_SEER: "预言家查验",
    Phase.NIGHT_WITCH: "女巫行动",
    Phase.NIGHT_RESOLVE: "夜间结算",
    Phase.DAY_ANNOUNCE: "天亮公布",
    Phase.DAY_DISCUSSION: "白天讨论",
    Phase.DAY_VOTE: "白天投票",
    Phase.DAY_PK_DISCUSSION: "PK 发言",
    Phase.DAY_PK_VOTE: "PK 投票",
    Phase.DAY_EXILE: "放逐结算",
    Phase.WIN_CHECK: "胜负判定",
    Phase.GAME_END: "游戏结束",
}


class ApplyOutcome(StrictModel):
    next_state: GameState
    events: tuple[DomainEvent, ...]
    error_code: CommandErrorCode | None = None


def deterministic_uuid(namespace: UUID, *parts: object) -> UUID:
    value = ":".join(str(part) for part in parts)
    return uuid5(namespace, value)


def _private_fact(
    event: DomainEvent,
    recipient_seat_id: int,
    fact_type: str,
    payload: dict[str, JsonValue],
    *,
    fact_ordinal: int = 1,
) -> PrivateFact:
    return PrivateFact(
        fact_id=deterministic_uuid(
            event.event_id,
            recipient_seat_id,
            fact_type,
            fact_ordinal,
        ),
        event_id=event.event_id,
        recipient_seat_id=recipient_seat_id,
        revision=event.revision,
        fact_type=fact_type,
        payload=payload,
    )


def _append_private_facts(
    state: GameState,
    facts: tuple[PrivateFact, ...],
) -> GameState:
    if not facts:
        return state
    known_fact_ids = {fact.fact_id for fact in state.private_facts}
    new_facts = tuple(fact for fact in facts if fact.fact_id not in known_fact_ids)
    if not new_facts:
        return state
    return state.model_copy(update={"private_facts": (*state.private_facts, *new_facts)})


def _wolf_decision_payload(decision: WolfDecision) -> dict[str, JsonValue]:
    return {
        "nominations": [
            {
                "seat_id": nomination.seat_id,
                "target_seat_id": nomination.target_seat_id,
            }
            for nomination in decision.nominations
        ],
        "target_seat_id": decision.target_seat_id,
        "locked": decision.locked,
    }


def _initial_wolf_decision_facts(
    state: GameState,
    event: DomainEvent,
) -> tuple[PrivateFact, ...]:
    return tuple(
        _private_fact(
            event,
            recipient_seat_id,
            "WOLF_DECISION",
            _wolf_decision_payload(state.wolf_decision),
            fact_ordinal=ordinal,
        )
        for ordinal, recipient_seat_id in enumerate(
            alive_wolf_ids(state),
            start=1,
        )
    )


def faction_of_role(role: Role) -> Faction:
    return Faction.WEREWOLF if role is Role.WEREWOLF else Faction.GOOD


def alive_wolf_ids(state: GameState) -> tuple[int, ...]:
    return tuple(
        player.seat_id for player in state.players if player.alive and player.role is Role.WEREWOLF
    )


def winner_for(state: GameState) -> Faction | None:
    wolves = sum(1 for player in state.players if player.alive and player.role is Role.WEREWOLF)
    good = sum(1 for player in state.players if player.alive and player.role is not Role.WEREWOLF)
    if wolves == 0:
        return Faction.GOOD
    if wolves >= good:
        return Faction.WEREWOLF
    return None


def lock_wolf_target(state: GameState) -> int | None:
    wolves = alive_wolf_ids(state)
    latest = {
        nomination.seat_id: nomination.target_seat_id
        for nomination in state.wolf_decision.nominations
        if nomination.seat_id in wolves
    }
    if len(wolves) == 1:
        return latest.get(wolves[0])
    if set(latest) == set(wolves) and len(set(latest.values())) == 1:
        return next(iter(latest.values()))
    return None


def _reachable_phases() -> frozenset[Phase]:
    reachable: set[Phase] = set()
    pending = [Phase.LOBBY]
    while pending:
        phase = pending.pop()
        if phase in reachable:
            continue
        reachable.add(phase)
        pending.extend(ALLOWED_TRANSITIONS.get(phase, frozenset()))
    return frozenset(reachable)


def validate_invariants(state: GameState) -> None:
    seats = tuple(player.seat_id for player in state.players)
    if len(seats) > 6:
        raise ValueError("game state cannot contain more than six players")
    if len(set(seats)) != len(seats):
        raise ValueError("player seats must be unique")
    if any(seat_id < 1 or seat_id > 6 for seat_id in seats):
        raise ValueError("player seats must be between 1 and 6")

    if state.phase is not Phase.LOBBY:
        roles = Counter(player.role for player in state.players if player.role is not None)
        if len(state.players) != 6 or roles != Counter(ROLE_COUNTS):
            raise ValueError("role counts must match RulePack v1")

    if state.winner is not None and state.phase is not Phase.GAME_END:
        raise ValueError("winner can only be set in GAME_END")

    if state.paused != (state.paused_at is not None):
        raise ValueError("pause state and pause timestamp must agree")

    if state.phase not in _reachable_phases():
        raise ValueError("phase is not reachable through ALLOWED_TRANSITIONS")

    dead_seat_ids = {player.seat_id for player in state.players if not player.alive}
    if state.discussion is not None and state.discussion.current_seat_id in dead_seat_ids:
        raise ValueError("dead player cannot be the current speaker")
    if (
        state.vote_round is not None
        and not state.vote_round.closed
        and dead_seat_ids.intersection(state.vote_round.eligible_voter_ids)
    ):
        raise ValueError("dead player cannot be an eligible voter")
    if state.phase in {Phase.NIGHT_WOLF, Phase.NIGHT_SEER, Phase.NIGHT_WITCH} and any(
        nomination.seat_id in dead_seat_ids for nomination in state.wolf_decision.nominations
    ):
        raise ValueError("dead player cannot be an active night actor")


def initial_state(room_id: UUID, seed: int) -> GameState:
    state = GameState(
        room_id=room_id,
        seed=seed,
        revision=0,
        phase=Phase.LOBBY,
        day=0,
    )
    validate_invariants(state)
    return state


def assign_roles(seed: int) -> tuple[RoleAssignment, ...]:
    roles = [role for role, count in ROLE_COUNTS.items() for _ in range(count)]
    random.Random(seed).shuffle(roles)
    return tuple(
        RoleAssignment(seat_id=seat_id, role=role) for seat_id, role in enumerate(roles, start=1)
    )


def transition(
    state: GameState,
    next_phase: Phase,
    events: list[DomainEvent],
    now: datetime,
) -> GameState:
    if next_phase not in ALLOWED_TRANSITIONS[state.phase]:
        raise ValueError(f"illegal transition from {state.phase} to {next_phase}")

    cause_id = (
        events[-1].causation_id
        if events and events[-1].causation_id is not None
        else deterministic_uuid(
            NAMESPACE_URL,
            state.room_id,
            state.revision,
            next_phase,
        )
    )
    event = build_event(
        cause_id=cause_id,
        event_ordinal=len(events) + 1,
        room_id=state.room_id,
        revision=state.revision,
        event_type=EventType.PHASE_CHANGED,
        visibility=PublicVisibility(),
        payload=PhaseChangedPayload(
            previous_phase=state.phase,
            next_phase=next_phase,
            day=state.day,
        ),
        created_at=now,
    )
    next_state = state.model_copy(update={"phase": next_phase})
    validate_invariants(next_state)
    events.append(event)
    return next_state


def _phase_statement_label(phase: Phase) -> str:
    return PHASE_STATEMENT_LABELS[phase]


def _public_statement(event: DomainEvent) -> str:
    payload = EVENT_PAYLOAD_MODELS[event.event_type].validate_json_payload(event.fact_payload)
    if isinstance(payload, PhaseChangedPayload):
        return f"进入第 {payload.day} 天 · {_phase_statement_label(payload.next_phase)}"
    if isinstance(payload, PlayersDiedPayload):
        if not payload.seat_ids:
            return "昨夜平安"
        seats = "、".join(f"{seat_id} 号" for seat_id in payload.seat_ids)
        return f"{seats} 玩家出局"
    if isinstance(payload, RoomJoinedPayload):
        return f"{payload.seat_id} 号玩家加入"
    if isinstance(payload, ReadyChangedPayload):
        state_text = "准备" if payload.ready else "取消准备"
        return f"{payload.seat_id} 号玩家{state_text}"
    if isinstance(payload, RoleConfirmedPayload):
        return f"{payload.seat_id} 号玩家已确认角色"
    if isinstance(payload, VoteRoundResolvedPayload):
        if payload.tie:
            return "投票平票"
        if payload.exiled_seat_id is None:
            return "本轮无人出局"
        return f"投票结果：{payload.exiled_seat_id} 号玩家得票最多"  # noqa: RUF001
    if isinstance(payload, PlayerExiledPayload):
        return f"{payload.seat_id} 号玩家被放逐"
    if isinstance(payload, NoExilePayload):
        return "本轮无人出局"
    if isinstance(payload, TimeoutAppliedPayload):
        return f"{_phase_statement_label(payload.phase)}阶段超时，系统自动推进"  # noqa: RUF001
    if isinstance(payload, HostPausedPayload):
        return "游戏已暂停"
    if isinstance(payload, HostResumedPayload):
        return "游戏已恢复"
    if isinstance(payload, SpeechRecordedPayload):
        return f"{payload.seat_id} 号玩家发言结束"
    if isinstance(payload, SpeechPassedPayload):
        return f"{payload.seat_id} 号玩家跳过发言"
    if isinstance(payload, GameEndedPayload):
        winner = "好人阵营" if payload.winner is Faction.GOOD else "狼人阵营"
        return f"游戏结束：{winner}获胜"  # noqa: RUF001
    return "公开事件已更新"


def _append_public_timeline(
    state: GameState,
    events: Sequence[DomainEvent],
) -> GameState:
    public_items = tuple(
        PublicTimelineItem(
            event_id=event.event_id,
            revision=event.revision,
            event_type=event.event_type,
            statement=_public_statement(event),
        )
        for event in events
        if event.visibility.scope == "public"
    )
    if not public_items:
        return state
    return state.model_copy(update={"public_timeline": (*state.public_timeline, *public_items)})


def _error(state: GameState, error_code: CommandErrorCode) -> ApplyOutcome:
    return ApplyOutcome(next_state=state, events=(), error_code=error_code)


def _enqueue_outbox(
    state: GameState,
    kind: Literal["dm.message", "view.updated", "game.ended"],
    event: DomainEvent,
) -> GameState:
    seq = state.outbox[-1].seq + 1 if state.outbox else 1
    item = OutboxItem(
        seq=seq,
        kind=kind,
        event_id=event.event_id,
        revision=event.revision,
    )
    return state.model_copy(update={"outbox": (*state.outbox, item)})


def _start_next_night(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> GameState:
    next_state = state.model_copy(
        update={
            "day": state.day + 1,
            "wolf_decision": WolfDecision(),
            "night": NightState(),
            "discussion": None,
            "deadline_at": None,
        }
    )
    next_state = transition(next_state, Phase.NIGHT_START, events, now)
    deadline_at = now + timedelta(seconds=NIGHT_WOLF_SECONDS)
    next_state = next_state.model_copy(update={"deadline_at": deadline_at})
    next_state = transition(next_state, Phase.NIGHT_WOLF, events, now)
    next_state = _append_private_facts(
        next_state,
        _initial_wolf_decision_facts(next_state, events[-1]),
    )
    validate_invariants(next_state)
    return next_state


def _settle_win_check(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
    *,
    source: Literal["NIGHT", "DAY"],
) -> ApplyOutcome:
    assert state.phase is Phase.WIN_CHECK
    winner = winner_for(state)
    if winner is None:
        if source == "NIGHT":
            next_state = transition(state, Phase.DAY_DISCUSSION, events, now)
            next_state = _start_day_discussion(next_state, now)
        else:
            next_state = _start_next_night(state, events, now)
        validate_invariants(next_state)
        return ApplyOutcome(next_state=next_state, events=tuple(events))

    next_state = transition(state, Phase.GAME_END, events, now)
    cause_id = (
        events[-1].causation_id
        if events and events[-1].causation_id is not None
        else deterministic_uuid(
            NAMESPACE_URL,
            state.room_id,
            state.revision,
            Phase.GAME_END,
        )
    )
    game_ended_event = build_event(
        cause_id=cause_id,
        event_ordinal=len(events) + 1,
        room_id=state.room_id,
        revision=state.revision,
        event_type=EventType.GAME_ENDED,
        visibility=PublicVisibility(),
        payload=GameEndedPayload(winner=winner),
        created_at=now,
    )
    events.append(game_ended_event)
    next_state = next_state.model_copy(update={"winner": winner})
    next_state = _enqueue_outbox(next_state, "game.ended", game_ended_event)
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _authorize_seat(
    state: GameState,
    actor: AuthenticatedActor | None,
    seat_id: int,
) -> CommandErrorCode | None:
    if (
        actor is None
        or actor.actor_type != "seat"
        or actor.seat_id != seat_id
        or actor.room_id != state.room_id
    ):
        return CommandErrorCode.ACTOR_NOT_AUTHORIZED
    return None


def _authorize_host(
    state: GameState,
    actor: AuthenticatedActor | None,
) -> CommandErrorCode | None:
    if (
        actor is None
        or actor.actor_type != "host"
        or actor.seat_id is not None
        or actor.room_id != state.room_id
    ):
        return CommandErrorCode.ACTOR_NOT_AUTHORIZED
    return None


def _players_with(
    players: tuple[Player, ...],
    updated: Player,
) -> tuple[Player, ...]:
    return tuple(updated if player.seat_id == updated.seat_id else player for player in players)


def _player_at(state: GameState, seat_id: int) -> Player | None:
    return next(
        (player for player in state.players if player.seat_id == seat_id),
        None,
    )


def _first_living_from(state: GameState, seat_id: int) -> int:
    ordered = tuple(player.seat_id for player in state.players)
    start = ordered.index(seat_id)
    for candidate in ordered[start:] + ordered[:start]:
        if state.players[candidate - 1].alive:
            return candidate
    raise ValueError("no living player")


def _start_day_discussion(state: GameState, now: datetime) -> GameState:
    living_seat_ids = tuple(player.seat_id for player in state.players if player.alive)
    cursor = state.next_discussion_cursor if state.day > 1 else 1
    current_seat_id = _first_living_from(state, cursor or 1)
    start_index = living_seat_ids.index(current_seat_id)
    participant_seat_ids = living_seat_ids[start_index:] + living_seat_ids[:start_index]
    deadline_at = now + timedelta(seconds=DISCUSSION_SECONDS)
    return state.model_copy(
        update={
            "discussion": DiscussionState(
                participant_seat_ids=participant_seat_ids,
                current_seat_id=current_seat_id,
                deadline_at=deadline_at,
            ),
            "deadline_at": deadline_at,
        }
    )


def _advance_discussion(
    state: GameState,
    next_revision: int,
    events: list[DomainEvent],
    speaker_seat_id: int,
    *,
    skipped: bool,
    now: datetime,
) -> ApplyOutcome:
    assert state.discussion is not None
    completed_seat_ids = (*state.discussion.completed_seat_ids, speaker_seat_id)
    skipped_seat_ids = (
        (*state.discussion.skipped_seat_ids, speaker_seat_id)
        if skipped
        else state.discussion.skipped_seat_ids
    )
    remaining = tuple(
        seat_id
        for seat_id in state.discussion.participant_seat_ids
        if seat_id not in completed_seat_ids
    )
    if remaining:
        seconds = (
            DISCUSSION_SECONDS if state.phase is Phase.DAY_DISCUSSION else PK_DISCUSSION_SECONDS
        )
        deadline_at = now + timedelta(seconds=seconds)
        current_seat_id = remaining[0]
        next_state = state.model_copy(
            update={
                "revision": next_revision,
                "discussion": state.discussion.model_copy(
                    update={
                        "completed_seat_ids": completed_seat_ids,
                        "skipped_seat_ids": skipped_seat_ids,
                        "current_seat_id": current_seat_id,
                        "deadline_at": deadline_at,
                    }
                ),
                "deadline_at": deadline_at,
            }
        )
    else:
        next_state = state.model_copy(
            update={
                "revision": next_revision,
                "discussion": state.discussion.model_copy(
                    update={
                        "completed_seat_ids": completed_seat_ids,
                        "skipped_seat_ids": skipped_seat_ids,
                        "current_seat_id": None,
                        "deadline_at": None,
                    }
                ),
                "deadline_at": None,
            }
        )
        if state.phase is Phase.DAY_PK_DISCUSSION:
            return _enter_pk_vote(next_state, events, now)
        return _enter_day_vote(next_state, events, now)
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _enter_day_vote(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> ApplyOutcome:
    deadline_at = now + timedelta(seconds=NORMAL_VOTE_SECONDS)
    vote_round = VoteRound(
        round_id=deterministic_uuid(
            state.room_id,
            state.revision,
            Phase.DAY_VOTE,
            1,
        ),
        round_index=1,
        phase=Phase.DAY_VOTE,
        eligible_voter_ids=tuple(player.seat_id for player in state.players if player.alive),
        opened_at=now,
        deadline_at=deadline_at,
    )
    next_state = state.model_copy(
        update={
            "vote_round": vote_round,
            "deadline_at": deadline_at,
        }
    )
    next_state = transition(next_state, Phase.DAY_VOTE, events, now)
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def tally_votes(round_: VoteRound) -> tuple[tuple[SeatTally, ...], int]:
    counts: dict[int, int] = {}
    abstentions = 0
    for vote in round_.votes:
        if vote.target_seat_id is None:
            abstentions += 1
        else:
            counts[vote.target_seat_id] = counts.get(vote.target_seat_id, 0) + 1
    tallies = tuple(SeatTally(seat_id=seat_id, votes=counts[seat_id]) for seat_id in sorted(counts))
    return tallies, abstentions


def _day_first_speaker(state: GameState) -> int:
    if state.discussion is not None and state.discussion.participant_seat_ids:
        return state.discussion.participant_seat_ids[0]
    if state.next_discussion_cursor is not None:
        return state.next_discussion_cursor
    return min(player.seat_id for player in state.players if player.alive)


def _enter_pk_discussion(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> ApplyOutcome:
    assert state.vote_round is not None
    candidates = state.vote_round.candidate_seat_ids
    if not candidates:
        raise ValueError("PK discussion requires at least one candidate")
    next_state = transition(state, Phase.DAY_PK_DISCUSSION, events, now)
    deadline_at = now + timedelta(seconds=PK_DISCUSSION_SECONDS)
    next_state = next_state.model_copy(
        update={
            "discussion": DiscussionState(
                participant_seat_ids=candidates,
                current_seat_id=candidates[0],
                deadline_at=deadline_at,
            ),
            "deadline_at": deadline_at,
        }
    )
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _enter_pk_vote(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> ApplyOutcome:
    assert state.vote_round is not None
    candidates = state.vote_round.candidate_seat_ids
    eligible_voter_ids = tuple(
        player.seat_id
        for player in state.players
        if player.alive and player.seat_id not in candidates
    )
    deadline_at = now + timedelta(seconds=PK_VOTE_SECONDS)
    vote_round = VoteRound(
        round_id=deterministic_uuid(
            state.room_id,
            state.revision,
            Phase.DAY_PK_VOTE,
            2,
        ),
        round_index=2,
        phase=Phase.DAY_PK_VOTE,
        candidate_seat_ids=candidates,
        eligible_voter_ids=eligible_voter_ids,
        opened_at=now,
        deadline_at=deadline_at,
    )
    next_state = transition(state, Phase.DAY_PK_VOTE, events, now)
    next_state = next_state.model_copy(
        update={
            "vote_round": vote_round,
            "deadline_at": deadline_at,
        }
    )
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _settle_vote_round(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> ApplyOutcome:
    assert state.vote_round is not None
    round_ = state.vote_round
    tallies, _ = tally_votes(round_)
    max_votes = max((tally.votes for tally in tallies), default=0)
    leaders = (
        tuple(tally.seat_id for tally in tallies if tally.votes == max_votes)
        if max_votes > 0
        else ()
    )
    is_pk = round_.phase is Phase.DAY_PK_VOTE
    exiled_seat_id = leaders[0] if len(leaders) == 1 else None
    is_tie = len(leaders) >= 2
    cause_id = next(
        (event.causation_id for event in events if event.causation_id is not None),
        round_.round_id,
    )
    events.append(
        build_event(
            cause_id=cause_id,
            event_ordinal=len(events) + 1,
            room_id=state.room_id,
            revision=state.revision,
            event_type=EventType.VOTE_ROUND_RESOLVED,
            visibility=PublicVisibility(),
            payload=VoteRoundResolvedPayload(
                round_id=round_.round_id,
                tallies=tallies,
                exiled_seat_id=exiled_seat_id,
                tie=is_tie,
            ),
            created_at=now,
        )
    )
    closed_round = round_.model_copy(
        update={
            "candidate_seat_ids": leaders,
            "closed": True,
        }
    )

    if exiled_seat_id is not None:
        players = tuple(
            player.model_copy(update={"alive": False})
            if player.seat_id == exiled_seat_id
            else player
            for player in state.players
        )
        next_state = state.model_copy(
            update={
                "players": players,
                "vote_round": closed_round,
                "next_discussion_cursor": exiled_seat_id,
                "deadline_at": None,
            }
        )
        exiled_event = build_event(
            cause_id=cause_id,
            event_ordinal=len(events) + 1,
            room_id=state.room_id,
            revision=state.revision,
            event_type=EventType.PLAYER_EXILED,
            visibility=PublicVisibility(),
            payload=PlayerExiledPayload(seat_id=exiled_seat_id),
            created_at=now,
        )
        events.append(exiled_event)
        next_state = _enqueue_outbox(next_state, "dm.message", exiled_event)
        next_state = transition(next_state, Phase.DAY_EXILE, events, now)
        next_state = transition(next_state, Phase.WIN_CHECK, events, now)
        return _settle_win_check(next_state, events, now, source="DAY")

    first_speaker = _day_first_speaker(state)
    if not is_pk and is_tie:
        next_state = state.model_copy(
            update={
                "vote_round": closed_round,
                "next_discussion_cursor": first_speaker,
                "deadline_at": None,
            }
        )
        return _enter_pk_discussion(next_state, events, now)

    reason: Literal["NO_VOTES", "PK_TIE", "NO_VALID_PK_VOTES"] = (
        "PK_TIE" if is_pk and is_tie else "NO_VALID_PK_VOTES" if is_pk else "NO_VOTES"
    )
    next_state = state.model_copy(
        update={
            "vote_round": closed_round,
            "next_discussion_cursor": state.next_discussion_cursor if is_pk else first_speaker,
            "deadline_at": None,
        }
    )
    no_exile_event = build_event(
        cause_id=cause_id,
        event_ordinal=len(events) + 1,
        room_id=state.room_id,
        revision=state.revision,
        event_type=EventType.NO_EXILE,
        visibility=PublicVisibility(),
        payload=NoExilePayload(reason=reason),
        created_at=now,
    )
    events.append(no_exile_event)
    next_state = _enqueue_outbox(next_state, "dm.message", no_exile_event)
    next_state = transition(next_state, Phase.WIN_CHECK, events, now)
    return _settle_win_check(next_state, events, now, source="DAY")


def _replace_vote(
    round_: VoteRound,
    voter_seat_id: int,
    target_seat_id: int | None,
) -> VoteRound:
    votes = list(round_.votes)
    for index, vote in enumerate(votes):
        if vote.voter_seat_id != voter_seat_id:
            continue
        if vote.target_seat_id == target_seat_id:
            return round_
        votes[index] = Vote(voter_seat_id=voter_seat_id, target_seat_id=target_seat_id)
        break
    else:
        votes.append(Vote(voter_seat_id=voter_seat_id, target_seat_id=target_seat_id))
    return round_.model_copy(update={"votes": tuple(votes)})


def _vote_actor(
    state: GameState,
    actor: AuthenticatedActor | None,
) -> tuple[Player | None, CommandErrorCode | None]:
    round_ = state.vote_round
    if round_ is not None and round_.closed:
        return None, CommandErrorCode.VOTE_ROUND_CLOSED
    if (
        state.paused
        or round_ is None
        or state.phase not in {Phase.DAY_VOTE, Phase.DAY_PK_VOTE}
        or round_.phase is not state.phase
    ):
        return None, CommandErrorCode.ILLEGAL_PHASE
    if actor is None or actor.seat_id is None:
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    authorization_error = _authorize_seat(state, actor, actor.seat_id)
    if authorization_error is not None:
        return None, authorization_error
    player = _player_at(state, actor.seat_id)
    if player is None:
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    if not player.alive:
        return None, CommandErrorCode.PLAYER_DEAD
    if player.seat_id not in round_.eligible_voter_ids:
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    return player, None


def _submit_vote(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    target_seat_id: int | None,
    now: datetime,
) -> ApplyOutcome:
    voter, error_code = _vote_actor(state, actor)
    if error_code is not None:
        return _error(state, error_code)
    assert voter is not None
    assert state.vote_round is not None

    if target_seat_id is not None:
        target = _player_at(state, target_seat_id)
        if (
            target is None
            or not target.alive
            or target.seat_id == voter.seat_id
            or (
                state.phase is Phase.DAY_PK_VOTE
                and target.seat_id not in state.vote_round.candidate_seat_ids
            )
        ):
            return _error(state, CommandErrorCode.INVALID_TARGET)

    updated_round = _replace_vote(
        state.vote_round,
        voter.seat_id,
        target_seat_id,
    )
    if updated_round == state.vote_round:
        return ApplyOutcome(next_state=state, events=())

    next_revision = state.revision + 1
    if target_seat_id is None:
        event = build_event(
            cause_id=command_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.ABSTAIN_RECORDED,
            visibility=HostVisibility(),
            payload=AbstainRecordedPayload(voter_seat_id=voter.seat_id),
            created_at=now,
        )
    else:
        event = build_event(
            cause_id=command_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.VOTE_RECORDED,
            visibility=HostVisibility(),
            payload=VoteRecordedPayload(
                voter_seat_id=voter.seat_id,
                target_seat_id=target_seat_id,
            ),
            created_at=now,
        )
    events = [event]
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "vote_round": updated_round,
        }
    )
    if len(updated_round.votes) == len(updated_round.eligible_voter_ids):
        return _settle_vote_round(next_state, events, now)
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _current_speaker(
    state: GameState,
    actor: AuthenticatedActor | None,
) -> tuple[Player | None, CommandErrorCode | None]:
    if state.phase not in {Phase.DAY_DISCUSSION, Phase.DAY_PK_DISCUSSION} or state.paused:
        return None, CommandErrorCode.ILLEGAL_PHASE
    if actor is None or actor.seat_id is None:
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    authorization_error = _authorize_seat(state, actor, actor.seat_id)
    if authorization_error is not None:
        return None, authorization_error
    player = _player_at(state, actor.seat_id)
    if player is None:
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    if not player.alive:
        return None, CommandErrorCode.PLAYER_DEAD
    if state.phase is Phase.DAY_PK_DISCUSSION and (
        state.vote_round is None or player.seat_id not in state.vote_round.candidate_seat_ids
    ):
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    if state.discussion is None or state.discussion.current_seat_id != player.seat_id:
        return None, CommandErrorCode.NOT_CURRENT_SPEAKER
    return player, None


def replace_players_alive(
    state: GameState,
    dead_seat_ids: tuple[int, ...],
    alive: bool,
    now: datetime,
) -> GameState:
    dead = set(dead_seat_ids)
    players = tuple(
        player.model_copy(update={"alive": alive}) if player.seat_id in dead else player
        for player in state.players
    )
    return state.model_copy(update={"players": players, "deadline_at": now})


def resolve_night(state: GameState, now: datetime) -> GameState:
    wolf_target = state.wolf_decision.target_seat_id
    deaths: set[int] = set()
    if wolf_target is not None and state.night.action != "ANTIDOTE":
        deaths.add(wolf_target)
    if state.night.action == "POISON" and state.night.poison_target_seat_id is not None:
        deaths.add(state.night.poison_target_seat_id)
    ordered = tuple(player.seat_id for player in state.players if player.seat_id in deaths)
    resolved = replace_players_alive(state, ordered, False, now)
    return resolved.model_copy(
        update={
            "night": state.night.model_copy(update={"deaths": ordered}),
            "deadline_at": None,
        }
    )


def _players_died_cause(
    wolf_target: int | None,
    wolf_death: bool,
    poison_death: bool,
) -> DeathCause:
    if wolf_target is None and not poison_death:
        return "WOLF"
    if wolf_death and poison_death:
        return "MIXED"
    if poison_death:
        return "POISON"
    return "WOLF"


def _resolve_night_phase(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> GameState:
    wolf_target = state.wolf_decision.target_seat_id
    wolf_death = wolf_target is not None and state.night.action != "ANTIDOTE"
    poison_death = state.night.action == "POISON" and state.night.poison_target_seat_id is not None
    resolved = resolve_night(state, now)
    next_state = transition(resolved, Phase.NIGHT_RESOLVE, events, now)
    cause_id = (
        events[-1].causation_id
        if events[-1].causation_id is not None
        else deterministic_uuid(NAMESPACE_URL, state.room_id, state.revision)
    )
    players_died_event = build_event(
        cause_id=cause_id,
        event_ordinal=len(events) + 1,
        room_id=state.room_id,
        revision=state.revision,
        event_type=EventType.PLAYERS_DIED,
        visibility=PublicVisibility(),
        payload=PlayersDiedPayload(
            seat_ids=resolved.night.deaths,
        ),
        created_at=now,
    )
    events.append(players_died_event)
    events.append(
        build_event(
            cause_id=cause_id,
            event_ordinal=len(events) + 1,
            room_id=state.room_id,
            revision=state.revision,
            event_type=EventType.PLAYERS_DIED,
            visibility=HostVisibility(),
            payload=PlayersDiedPayload(
                seat_ids=resolved.night.deaths,
                cause=_players_died_cause(wolf_target, wolf_death, poison_death),
            ),
            created_at=now,
        )
    )
    next_state = _enqueue_outbox(next_state, "dm.message", players_died_event)
    next_state = transition(next_state, Phase.DAY_ANNOUNCE, events, now)
    next_state = transition(next_state, Phase.WIN_CHECK, events, now)
    return _settle_win_check(next_state, events, now, source="NIGHT").next_state


def _settle_witch_action(
    state: GameState,
    cause_id: UUID,
    next_revision: int,
    events: list[DomainEvent],
    witch_seat_id: int,
    action: WitchAction,
    target_seat_id: int | None,
    potions: PotionState,
    now: datetime,
) -> ApplyOutcome:
    action_event = build_event(
        cause_id=cause_id,
        event_ordinal=len(events) + 1,
        room_id=state.room_id,
        revision=next_revision,
        event_type=EventType.WITCH_ACTION_RECORDED,
        visibility=SeatVisibility(seat_id=witch_seat_id),
        payload=WitchActionPayload(
            witch_seat_id=witch_seat_id,
            action=action,
            target_seat_id=target_seat_id,
        ),
        created_at=now,
    )
    events.append(action_event)
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "potions": potions,
            "night": NightState(
                action=action,
                poison_target_seat_id=target_seat_id,
            ),
            "deadline_at": None,
        }
    )
    next_state = _append_private_facts(
        next_state,
        (
            _private_fact(
                action_event,
                witch_seat_id,
                "WITCH_POTIONS",
                {
                    "antidote_available": potions.antidote_available,
                    "poison_available": potions.poison_available,
                },
            ),
        ),
    )
    return ApplyOutcome(
        next_state=_resolve_night_phase(next_state, events, now),
        events=tuple(events),
    )


def _witch_action_player(
    state: GameState,
    actor: AuthenticatedActor | None,
) -> tuple[Player | None, CommandErrorCode | None]:
    if state.phase is not Phase.NIGHT_WITCH or state.paused:
        return None, CommandErrorCode.ILLEGAL_PHASE
    if actor is None or actor.seat_id is None:
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    authorization_error = _authorize_seat(state, actor, actor.seat_id)
    if authorization_error is not None:
        return None, authorization_error
    witch = _player_at(state, actor.seat_id)
    if witch is None or witch.role is not Role.WITCH:
        return None, CommandErrorCode.ACTOR_NOT_AUTHORIZED
    if not witch.alive:
        return None, CommandErrorCode.PLAYER_DEAD
    return witch, None


def _witch_use_antidote(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    now: datetime,
) -> ApplyOutcome:
    witch, error_code = _witch_action_player(state, actor)
    if error_code is not None:
        return _error(state, error_code)
    assert witch is not None
    if not state.potions.antidote_available:
        return _error(state, CommandErrorCode.POTION_ALREADY_USED)
    if state.wolf_decision.target_seat_id is None:
        return _error(state, CommandErrorCode.INVALID_TARGET)
    if state.wolf_decision.target_seat_id == witch.seat_id:
        return _error(state, CommandErrorCode.WITCH_SELF_RESCUE_FORBIDDEN)
    return _settle_witch_action(
        state,
        command_id,
        state.revision + 1,
        [],
        witch.seat_id,
        "ANTIDOTE",
        None,
        state.potions.model_copy(update={"antidote_available": False}),
        now,
    )


def _witch_use_poison(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    payload: WitchUsePoisonCommand,
    now: datetime,
) -> ApplyOutcome:
    witch, error_code = _witch_action_player(state, actor)
    if error_code is not None:
        return _error(state, error_code)
    assert witch is not None
    if not state.potions.poison_available:
        return _error(state, CommandErrorCode.POTION_ALREADY_USED)
    target = _player_at(state, payload.target_seat_id)
    if target is None or not target.alive or target.seat_id == witch.seat_id:
        return _error(state, CommandErrorCode.INVALID_TARGET)
    return _settle_witch_action(
        state,
        command_id,
        state.revision + 1,
        [],
        witch.seat_id,
        "POISON",
        target.seat_id,
        state.potions.model_copy(update={"poison_available": False}),
        now,
    )


def _witch_skip(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    now: datetime,
) -> ApplyOutcome:
    witch, error_code = _witch_action_player(state, actor)
    if error_code is not None:
        return _error(state, error_code)
    assert witch is not None
    return _settle_witch_action(
        state,
        command_id,
        state.revision + 1,
        [],
        witch.seat_id,
        "SKIP",
        None,
        state.potions,
        now,
    )


def _speak(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    payload: SpeakCommand,
    now: datetime,
) -> ApplyOutcome:
    speaker, error_code = _current_speaker(state, actor)
    if error_code is not None:
        return _error(state, error_code)
    assert speaker is not None
    next_revision = state.revision + 1
    events = [
        build_event(
            cause_id=command_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.SPEECH_RECORDED,
            visibility=PublicVisibility(),
            payload=SpeechRecordedPayload(
                seat_id=speaker.seat_id,
                text=payload.text,
            ),
            created_at=now,
        )
    ]
    return _advance_discussion(
        state,
        next_revision,
        events,
        speaker.seat_id,
        skipped=False,
        now=now,
    )


def _pass_speech(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    now: datetime,
) -> ApplyOutcome:
    speaker, error_code = _current_speaker(state, actor)
    if error_code is not None:
        return _error(state, error_code)
    assert speaker is not None
    next_revision = state.revision + 1
    events = [
        build_event(
            cause_id=command_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.SPEECH_PASSED,
            visibility=PublicVisibility(),
            payload=SpeechPassedPayload(
                seat_id=speaker.seat_id,
                timed_out=False,
            ),
            created_at=now,
        )
    ]
    return _advance_discussion(
        state,
        next_revision,
        events,
        speaker.seat_id,
        skipped=True,
        now=now,
    )


def _apply_discussion_timeout(
    state: GameState,
    timeout: SystemTimeout,
    now: datetime,
) -> ApplyOutcome:
    if (
        state.paused
        or timeout.phase not in {Phase.DAY_DISCUSSION, Phase.DAY_PK_DISCUSSION}
        or state.phase is not timeout.phase
        or state.discussion is None
        or state.discussion.current_seat_id is None
        or state.discussion.deadline_at is None
        or now < state.discussion.deadline_at
    ):
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if timeout.expected_revision != state.revision:
        return _error(state, CommandErrorCode.REVISION_CONFLICT)

    next_revision = state.revision + 1
    speaker_seat_id = state.discussion.current_seat_id
    events = [
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.TIMEOUT_APPLIED,
            visibility=PublicVisibility(),
            payload=TimeoutAppliedPayload(
                phase=timeout.phase,
                timeout_reason="SPEECH",
            ),
            created_at=now,
        ),
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=2,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.SPEECH_PASSED,
            visibility=PublicVisibility(),
            payload=SpeechPassedPayload(
                seat_id=speaker_seat_id,
                timed_out=True,
            ),
            created_at=now,
        ),
    ]
    return _advance_discussion(
        state,
        next_revision,
        events,
        speaker_seat_id,
        skipped=True,
        now=now,
    )


def _apply_vote_timeout(
    state: GameState,
    timeout: SystemTimeout,
    now: datetime,
) -> ApplyOutcome:
    if (
        state.paused
        or timeout.phase not in {Phase.DAY_VOTE, Phase.DAY_PK_VOTE}
        or state.phase is not timeout.phase
        or state.vote_round is None
        or state.vote_round.closed
        or state.vote_round.deadline_at is None
        or now < state.vote_round.deadline_at
    ):
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if timeout.expected_revision != state.revision:
        return _error(state, CommandErrorCode.REVISION_CONFLICT)

    next_revision = state.revision + 1
    timeout_reason: Literal["VOTE", "PK_VOTE"] = (
        "VOTE" if timeout.phase is Phase.DAY_VOTE else "PK_VOTE"
    )
    events = [
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.TIMEOUT_APPLIED,
            visibility=PublicVisibility(),
            payload=TimeoutAppliedPayload(
                phase=timeout.phase,
                timeout_reason=timeout_reason,
            ),
            created_at=now,
        )
    ]
    submitted = {vote.voter_seat_id for vote in state.vote_round.votes}
    votes = list(state.vote_round.votes)
    for voter_seat_id in state.vote_round.eligible_voter_ids:
        if voter_seat_id in submitted:
            continue
        votes.append(Vote(voter_seat_id=voter_seat_id, target_seat_id=None))
        events.append(
            build_event(
                cause_id=timeout.timeout_id,
                event_ordinal=len(events) + 1,
                room_id=state.room_id,
                revision=next_revision,
                event_type=EventType.ABSTAIN_RECORDED,
                visibility=HostVisibility(),
                payload=AbstainRecordedPayload(voter_seat_id=voter_seat_id),
                created_at=now,
            )
        )

    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "vote_round": state.vote_round.model_copy(update={"votes": tuple(votes)}),
        }
    )
    return _settle_vote_round(next_state, events, now)


def _join_room(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    payload: JoinRoomCommand,
    now: datetime,
) -> ApplyOutcome:
    if state.phase is not Phase.LOBBY or state.paused:
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)

    authorization_error = _authorize_seat(state, actor, payload.seat_id)
    if authorization_error is not None:
        return _error(state, authorization_error)

    existing = _player_at(state, payload.seat_id)
    if existing is not None:
        if existing.display_name == payload.display_name:
            return ApplyOutcome(next_state=state, events=())
        return _error(state, CommandErrorCode.INVALID_TARGET)

    next_revision = state.revision + 1
    player = Player(
        seat_id=payload.seat_id,
        display_name=payload.display_name,
        alive=True,
        connected=True,
        ready=False,
        role=None,
        role_confirmed=False,
    )
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "players": tuple(sorted((*state.players, player), key=lambda item: item.seat_id)),
        }
    )
    event = build_event(
        cause_id=command_id,
        event_ordinal=1,
        room_id=state.room_id,
        revision=next_revision,
        event_type=EventType.ROOM_JOINED,
        visibility=PublicVisibility(),
        payload=RoomJoinedPayload(
            seat_id=payload.seat_id,
            display_name=payload.display_name,
        ),
        created_at=now,
    )
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=(event,))


def _set_ready(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    payload: SetReadyCommand,
    now: datetime,
) -> ApplyOutcome:
    if state.phase is not Phase.LOBBY or state.paused:
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if actor is None or actor.seat_id is None:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)

    authorization_error = _authorize_seat(state, actor, actor.seat_id)
    if authorization_error is not None:
        return _error(state, authorization_error)

    player = _player_at(state, actor.seat_id)
    if player is None:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)
    if player.ready == payload.ready:
        return ApplyOutcome(next_state=state, events=())

    next_revision = state.revision + 1
    updated_player = player.model_copy(update={"ready": payload.ready})
    updated_players = _players_with(state.players, updated_player)
    events = [
        build_event(
            cause_id=command_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.READY_CHANGED,
            visibility=PublicVisibility(),
            payload=ReadyChangedPayload(
                seat_id=player.seat_id,
                ready=payload.ready,
            ),
            created_at=now,
        )
    ]

    if len(updated_players) == 6 and all(item.ready for item in updated_players):
        assignments = assign_roles(state.seed)
        roles = {assignment.seat_id: assignment.role for assignment in assignments}
        assigned_players = tuple(
            item.model_copy(
                update={
                    "role": roles[item.seat_id],
                    "role_confirmed": False,
                }
            )
            for item in updated_players
        )
        next_state = state.model_copy(
            update={
                "revision": next_revision,
                "players": assigned_players,
                "deadline_at": now + timedelta(seconds=ROLE_REVEAL_SECONDS),
            }
        )
        next_state = transition(next_state, Phase.ROLE_REVEAL, events, now)
        role_events: list[tuple[RoleAssignment, DomainEvent]] = []
        for assignment in assignments:
            event = build_event(
                cause_id=command_id,
                event_ordinal=len(events) + 1,
                room_id=state.room_id,
                revision=next_revision,
                event_type=EventType.ROLE_ASSIGNED,
                visibility=SeatVisibility(seat_id=assignment.seat_id),
                payload=RoleAssignedPayload(
                    seat_id=assignment.seat_id,
                    role=assignment.role,
                ),
                created_at=now,
            )
            events.append(event)
            role_events.append((assignment, event))

        private_facts: list[PrivateFact] = []
        wolf_ids = tuple(
            assignment.seat_id for assignment, _ in role_events if assignment.role is Role.WEREWOLF
        )
        if wolf_ids:
            wolf_team_event = next(
                event for assignment, event in role_events if assignment.role is Role.WEREWOLF
            )
            private_facts.extend(
                _private_fact(
                    wolf_team_event,
                    wolf_seat_id,
                    "WOLF_TEAM",
                    {"seat_ids": list(wolf_ids)},
                    fact_ordinal=ordinal,
                )
                for ordinal, wolf_seat_id in enumerate(wolf_ids, start=1)
            )
        for assignment, event in role_events:
            if assignment.role is Role.WITCH:
                private_facts.append(
                    _private_fact(
                        event,
                        assignment.seat_id,
                        "WITCH_POTIONS",
                        {
                            "antidote_available": next_state.potions.antidote_available,
                            "poison_available": next_state.potions.poison_available,
                        },
                    )
                )
        next_state = _append_private_facts(next_state, tuple(private_facts))
        return ApplyOutcome(next_state=next_state, events=tuple(events))

    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "players": updated_players,
        }
    )
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _confirm_role(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    now: datetime,
) -> ApplyOutcome:
    if state.phase is not Phase.ROLE_REVEAL or state.paused:
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if actor is None or actor.seat_id is None:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)

    authorization_error = _authorize_seat(state, actor, actor.seat_id)
    if authorization_error is not None:
        return _error(state, authorization_error)

    player = _player_at(state, actor.seat_id)
    if player is None:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)
    if player.role_confirmed:
        return ApplyOutcome(next_state=state, events=())

    next_revision = state.revision + 1
    updated_player = player.model_copy(update={"role_confirmed": True})
    updated_players = _players_with(state.players, updated_player)
    events = [
        build_event(
            cause_id=command_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.ROLE_CONFIRMED,
            visibility=PublicVisibility(),
            payload=RoleConfirmedPayload(seat_id=player.seat_id),
            created_at=now,
        )
    ]
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "players": updated_players,
        }
    )

    if all(item.role_confirmed for item in updated_players):
        next_state = next_state.model_copy(
            update={
                "day": 1,
                "deadline_at": now + timedelta(seconds=NIGHT_WOLF_SECONDS),
            }
        )
        next_state = transition(next_state, Phase.NIGHT_START, events, now)
        next_state = transition(next_state, Phase.NIGHT_WOLF, events, now)
        next_state = _append_private_facts(
            next_state,
            _initial_wolf_decision_facts(next_state, events[-1]),
        )

    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _enter_witch_phase(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> GameState:
    next_state = state.model_copy(
        update={"deadline_at": now + timedelta(seconds=NIGHT_WITCH_SECONDS)}
    )
    next_state = transition(next_state, Phase.NIGHT_WITCH, events, now)
    witch = next(
        (player for player in next_state.players if player.alive and player.role is Role.WITCH),
        None,
    )
    if witch is None:
        next_state = next_state.model_copy(update={"deadline_at": None})
        return _resolve_night_phase(next_state, events, now)

    phase_event = events[-1]
    next_state = _append_private_facts(
        next_state,
        (
            _private_fact(
                phase_event,
                witch.seat_id,
                "WITCH_KILL_TARGET",
                {"target_seat_id": next_state.wolf_decision.target_seat_id},
            ),
        ),
    )
    return next_state


def _enter_seer_phase(
    state: GameState,
    events: list[DomainEvent],
    now: datetime,
) -> GameState:
    next_state = state.model_copy(
        update={"deadline_at": now + timedelta(seconds=NIGHT_SEER_SECONDS)}
    )
    next_state = transition(next_state, Phase.NIGHT_SEER, events, now)
    if not any(player.alive and player.role is Role.SEER for player in next_state.players):
        next_state = next_state.model_copy(update={"deadline_at": None})
        next_state = _enter_witch_phase(next_state, events, now)
    return next_state


def _replace_wolf_nomination(
    decision: WolfDecision,
    actor_seat_id: int,
    target_seat_id: int,
) -> WolfDecision:
    nominations = list(decision.nominations)
    for index, nomination in enumerate(nominations):
        if nomination.seat_id != actor_seat_id:
            continue
        if nomination.target_seat_id == target_seat_id:
            return decision
        nominations[index] = WolfNomination(
            seat_id=actor_seat_id,
            target_seat_id=target_seat_id,
        )
        break
    else:
        nominations.append(
            WolfNomination(
                seat_id=actor_seat_id,
                target_seat_id=target_seat_id,
            )
        )
    return WolfDecision(nominations=tuple(nominations))


def _wolf_nominate_kill(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    payload: WolfNominateKillCommand,
    now: datetime,
) -> ApplyOutcome:
    if state.phase is not Phase.NIGHT_WOLF or state.paused:
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if actor is None or actor.seat_id is None:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)

    authorization_error = _authorize_seat(state, actor, actor.seat_id)
    if authorization_error is not None:
        return _error(state, authorization_error)

    wolf = _player_at(state, actor.seat_id)
    if wolf is None or wolf.role is not Role.WEREWOLF:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)
    if not wolf.alive:
        return _error(state, CommandErrorCode.PLAYER_DEAD)

    target = _player_at(state, payload.target_seat_id)
    if (
        target is None
        or not target.alive
        or target.role is None
        or target.role is Role.WEREWOLF
        or target.seat_id == wolf.seat_id
    ):
        return _error(state, CommandErrorCode.INVALID_TARGET)

    updated_decision = _replace_wolf_nomination(
        state.wolf_decision,
        wolf.seat_id,
        target.seat_id,
    )
    if updated_decision == state.wolf_decision:
        return ApplyOutcome(next_state=state, events=())

    next_revision = state.revision + 1
    with_nomination = state.model_copy(
        update={
            "revision": next_revision,
            "wolf_decision": updated_decision,
        }
    )
    locked_target = lock_wolf_target(with_nomination)
    nomination_decision = updated_decision
    nomination_event = build_event(
        cause_id=command_id,
        event_ordinal=1,
        room_id=state.room_id,
        revision=next_revision,
        event_type=EventType.WOLF_NOMINATION_RECORDED,
        visibility=FactionVisibility(faction=Faction.WEREWOLF),
        payload=WolfNominationPayload(
            seat_id=wolf.seat_id,
            target_seat_id=target.seat_id,
        ),
        created_at=now,
    )
    events = [nomination_event]
    private_facts = [
        _private_fact(
            nomination_event,
            recipient_seat_id,
            "WOLF_DECISION",
            _wolf_decision_payload(nomination_decision),
            fact_ordinal=ordinal,
        )
        for ordinal, recipient_seat_id in enumerate(
            alive_wolf_ids(with_nomination),
            start=1,
        )
    ]
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "wolf_decision": nomination_decision,
        }
    )
    if locked_target is not None:
        locked_decision = nomination_decision.model_copy(
            update={
                "target_seat_id": locked_target,
                "locked": True,
            }
        )
        locked_event = build_event(
            cause_id=command_id,
            event_ordinal=2,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.WOLF_TARGET_LOCKED,
            visibility=FactionVisibility(faction=Faction.WEREWOLF),
            payload=WolfTargetLockedPayload(target_seat_id=locked_target),
            created_at=now,
        )
        events.append(locked_event)
        private_facts.extend(
            _private_fact(
                locked_event,
                recipient_seat_id,
                "WOLF_DECISION",
                _wolf_decision_payload(locked_decision),
                fact_ordinal=ordinal,
            )
            for ordinal, recipient_seat_id in enumerate(
                alive_wolf_ids(with_nomination),
                start=1,
            )
        )
        next_state = next_state.model_copy(update={"wolf_decision": locked_decision})
        next_state = _append_private_facts(next_state, tuple(private_facts))
        next_state = _enter_seer_phase(next_state, events, now)
    else:
        next_state = _append_private_facts(next_state, tuple(private_facts))

    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _seer_inspect(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    payload: SeerInspectCommand,
    now: datetime,
) -> ApplyOutcome:
    if state.phase is not Phase.NIGHT_SEER or state.paused:
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if actor is None or actor.seat_id is None:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)

    authorization_error = _authorize_seat(state, actor, actor.seat_id)
    if authorization_error is not None:
        return _error(state, authorization_error)

    seer = _player_at(state, actor.seat_id)
    if seer is None or seer.role is not Role.SEER:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)
    if not seer.alive:
        return _error(state, CommandErrorCode.PLAYER_DEAD)

    target = _player_at(state, payload.target_seat_id)
    if target is None or not target.alive or target.seat_id == seer.seat_id:
        return _error(state, CommandErrorCode.INVALID_TARGET)
    if target.role is None:
        return _error(state, CommandErrorCode.INVALID_TARGET)

    next_revision = state.revision + 1
    checked = SeerCheckRecord(
        day=state.day,
        target_seat_id=target.seat_id,
        faction=faction_of_role(target.role),
    )
    events = [
        build_event(
            cause_id=command_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.SEER_CHECKED,
            visibility=SeatVisibility(seat_id=seer.seat_id),
            payload=SeerCheckedPayload(
                seer_seat_id=seer.seat_id,
                target_seat_id=target.seat_id,
                target_faction=checked.faction,
            ),
            created_at=now,
        )
    ]
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "seer_checks": (*state.seer_checks, checked),
            "deadline_at": None,
        }
    )
    next_state = _append_private_facts(
        next_state,
        (
            _private_fact(
                events[0],
                seer.seat_id,
                "SEER_CHECK",
                {
                    "day": checked.day,
                    "target_seat_id": checked.target_seat_id,
                    "faction": checked.faction.value,
                },
            ),
        ),
    )
    next_state = _enter_witch_phase(next_state, events, now)
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _apply_wolf_timeout(
    state: GameState,
    timeout: SystemTimeout,
    now: datetime,
) -> ApplyOutcome:
    if (
        state.paused
        or timeout.phase is not Phase.NIGHT_WOLF
        or state.phase is not Phase.NIGHT_WOLF
        or state.deadline_at is None
        or now < state.deadline_at
    ):
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if timeout.expected_revision != state.revision:
        return _error(state, CommandErrorCode.REVISION_CONFLICT)

    next_revision = state.revision + 1
    events = [
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.TIMEOUT_APPLIED,
            visibility=PublicVisibility(),
            payload=TimeoutAppliedPayload(
                phase=Phase.NIGHT_WOLF,
                timeout_reason="WOLF",
            ),
            created_at=now,
        ),
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=2,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.WOLF_TARGET_LOCKED,
            visibility=FactionVisibility(faction=Faction.WEREWOLF),
            payload=WolfTargetLockedPayload(target_seat_id=None),
            created_at=now,
        ),
    ]
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "wolf_decision": state.wolf_decision.model_copy(
                update={
                    "target_seat_id": None,
                    "locked": True,
                }
            ),
        }
    )
    locked_decision = next_state.wolf_decision
    next_state = _append_private_facts(
        next_state,
        tuple(
            _private_fact(
                events[1],
                recipient_seat_id,
                "WOLF_DECISION",
                _wolf_decision_payload(locked_decision),
                fact_ordinal=ordinal,
            )
            for ordinal, recipient_seat_id in enumerate(
                alive_wolf_ids(next_state),
                start=1,
            )
        ),
    )
    next_state = _enter_seer_phase(next_state, events, now)
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _apply_seer_timeout(
    state: GameState,
    timeout: SystemTimeout,
    now: datetime,
) -> ApplyOutcome:
    if (
        state.paused
        or timeout.phase is not Phase.NIGHT_SEER
        or state.phase is not Phase.NIGHT_SEER
        or state.deadline_at is None
        or now < state.deadline_at
    ):
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if timeout.expected_revision != state.revision:
        return _error(state, CommandErrorCode.REVISION_CONFLICT)

    next_revision = state.revision + 1
    events = [
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.TIMEOUT_APPLIED,
            visibility=PublicVisibility(),
            payload=TimeoutAppliedPayload(
                phase=Phase.NIGHT_SEER,
                timeout_reason="SEER",
            ),
            created_at=now,
        )
    ]
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "deadline_at": None,
        }
    )
    next_state = _enter_witch_phase(next_state, events, now)
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _apply_witch_timeout(
    state: GameState,
    timeout: SystemTimeout,
    now: datetime,
) -> ApplyOutcome:
    if (
        state.paused
        or timeout.phase is not Phase.NIGHT_WITCH
        or state.phase is not Phase.NIGHT_WITCH
        or state.deadline_at is None
        or now < state.deadline_at
    ):
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if timeout.expected_revision != state.revision:
        return _error(state, CommandErrorCode.REVISION_CONFLICT)

    witch = next(
        (player for player in state.players if player.alive and player.role is Role.WITCH),
        None,
    )
    if witch is None:
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    next_revision = state.revision + 1
    events = [
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.TIMEOUT_APPLIED,
            visibility=PublicVisibility(),
            payload=TimeoutAppliedPayload(
                phase=Phase.NIGHT_WITCH,
                timeout_reason="WITCH",
            ),
            created_at=now,
        )
    ]
    return _settle_witch_action(
        state,
        timeout.timeout_id,
        next_revision,
        events,
        witch.seat_id,
        "SKIP",
        None,
        state.potions,
        now,
    )


def _host_pause(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    payload: HostPauseCommand,
    now: datetime,
) -> ApplyOutcome:
    authorization_error = _authorize_host(state, actor)
    if authorization_error is not None:
        return _error(state, authorization_error)
    if state.paused:
        return ApplyOutcome(next_state=state, events=())

    next_revision = state.revision + 1
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "paused": True,
            "paused_at": now,
        }
    )
    event = build_event(
        cause_id=command_id,
        event_ordinal=1,
        room_id=state.room_id,
        revision=next_revision,
        event_type=EventType.HOST_PAUSED,
        visibility=PublicVisibility(),
        payload=HostPausedPayload(reason=payload.reason),
        created_at=now,
    )
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=(event,))


def _host_resume(
    state: GameState,
    command_id: UUID,
    actor: AuthenticatedActor | None,
    now: datetime,
) -> ApplyOutcome:
    authorization_error = _authorize_host(state, actor)
    if authorization_error is not None:
        return _error(state, authorization_error)
    if not state.paused:
        return ApplyOutcome(next_state=state, events=())

    assert state.paused_at is not None
    paused_duration = now - state.paused_at
    next_revision = state.revision + 1
    discussion = state.discussion
    if discussion is not None and discussion.deadline_at is not None:
        discussion = discussion.model_copy(
            update={"deadline_at": discussion.deadline_at + paused_duration}
        )
    vote_round = state.vote_round
    if vote_round is not None and vote_round.deadline_at is not None:
        vote_round = vote_round.model_copy(
            update={"deadline_at": vote_round.deadline_at + paused_duration}
        )
    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "paused": False,
            "paused_at": None,
            "deadline_at": (
                state.deadline_at + paused_duration if state.deadline_at is not None else None
            ),
            "discussion": discussion,
            "vote_round": vote_round,
        }
    )
    event = build_event(
        cause_id=command_id,
        event_ordinal=1,
        room_id=state.room_id,
        revision=next_revision,
        event_type=EventType.HOST_RESUMED,
        visibility=PublicVisibility(),
        payload=HostResumedPayload(),
        created_at=now,
    )
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=(event,))


def _apply_role_reveal_timeout(
    state: GameState,
    timeout: SystemTimeout,
    now: datetime,
) -> ApplyOutcome:
    if (
        state.paused
        or timeout.phase is not Phase.ROLE_REVEAL
        or state.phase is not Phase.ROLE_REVEAL
        or state.deadline_at is None
        or now < state.deadline_at
    ):
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if timeout.expected_revision != state.revision:
        return _error(state, CommandErrorCode.REVISION_CONFLICT)

    next_revision = state.revision + 1
    unconfirmed = tuple(player for player in state.players if not player.role_confirmed)
    updated_players = tuple(
        player.model_copy(update={"role_confirmed": True}) for player in state.players
    )
    events = [
        build_event(
            cause_id=timeout.timeout_id,
            event_ordinal=1,
            room_id=state.room_id,
            revision=next_revision,
            event_type=EventType.TIMEOUT_APPLIED,
            visibility=PublicVisibility(),
            payload=TimeoutAppliedPayload(
                phase=Phase.ROLE_REVEAL,
                timeout_reason="ROLE_CONFIRM",
            ),
            created_at=now,
        )
    ]
    for player in unconfirmed:
        events.append(
            build_event(
                cause_id=timeout.timeout_id,
                event_ordinal=len(events) + 1,
                room_id=state.room_id,
                revision=next_revision,
                event_type=EventType.ROLE_CONFIRMED,
                visibility=PublicVisibility(),
                payload=RoleConfirmedPayload(seat_id=player.seat_id),
                created_at=now,
            )
        )

    next_state = state.model_copy(
        update={
            "revision": next_revision,
            "players": updated_players,
            "day": 1,
            "deadline_at": now + timedelta(seconds=NIGHT_WOLF_SECONDS),
        }
    )
    next_state = transition(next_state, Phase.NIGHT_START, events, now)
    next_state = transition(next_state, Phase.NIGHT_WOLF, events, now)
    next_state = _append_private_facts(
        next_state,
        _initial_wolf_decision_facts(next_state, events[-1]),
    )
    validate_invariants(next_state)
    return ApplyOutcome(next_state=next_state, events=tuple(events))


def _apply_command_impl(
    state: GameState,
    trigger: CommandEnvelope | SystemTimeout,
    actor: AuthenticatedActor | None,
    now: datetime,
) -> ApplyOutcome:
    if trigger.room_id != state.room_id:
        return _error(state, CommandErrorCode.ACTOR_NOT_AUTHORIZED)

    if isinstance(trigger, SystemTimeout):
        if state.paused:
            return _error(state, CommandErrorCode.ILLEGAL_PHASE)
        if trigger.phase is Phase.ROLE_REVEAL:
            return _apply_role_reveal_timeout(state, trigger, now)
        if trigger.phase is Phase.NIGHT_WOLF:
            return _apply_wolf_timeout(state, trigger, now)
        if trigger.phase is Phase.NIGHT_SEER:
            return _apply_seer_timeout(state, trigger, now)
        if trigger.phase is Phase.NIGHT_WITCH:
            return _apply_witch_timeout(state, trigger, now)
        if trigger.phase in {Phase.DAY_DISCUSSION, Phase.DAY_PK_DISCUSSION}:
            return _apply_discussion_timeout(state, trigger, now)
        if trigger.phase in {Phase.DAY_VOTE, Phase.DAY_PK_VOTE}:
            return _apply_vote_timeout(state, trigger, now)
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)

    payload = trigger.payload
    if state.phase is Phase.GAME_END:
        return _error(state, CommandErrorCode.GAME_ENDED)

    if isinstance(
        payload,
        (
            HostPatchCommand,
            HostRewindToSnapshotCommand,
            HostForceTemplateCommand,
        ),
    ):
        authorization_error = _authorize_host(state, actor)
        if authorization_error is not None:
            return _error(state, authorization_error)
        return _error(state, CommandErrorCode.HOST_RECOVERY_NOT_IN_S1)

    if isinstance(payload, HostPauseCommand):
        return _host_pause(state, trigger.command_id, actor, payload, now)
    if isinstance(payload, HostResumeCommand):
        return _host_resume(state, trigger.command_id, actor, now)

    if state.paused:
        return _error(state, CommandErrorCode.ILLEGAL_PHASE)
    if isinstance(payload, JoinRoomCommand):
        return _join_room(state, trigger.command_id, actor, payload, now)
    if isinstance(payload, SetReadyCommand):
        return _set_ready(state, trigger.command_id, actor, payload, now)
    if isinstance(payload, ConfirmRoleCommand):
        return _confirm_role(state, trigger.command_id, actor, now)
    if isinstance(payload, WolfNominateKillCommand):
        return _wolf_nominate_kill(state, trigger.command_id, actor, payload, now)
    if isinstance(payload, SeerInspectCommand):
        return _seer_inspect(state, trigger.command_id, actor, payload, now)
    if isinstance(payload, WitchUseAntidoteCommand):
        return _witch_use_antidote(state, trigger.command_id, actor, now)
    if isinstance(payload, WitchUsePoisonCommand):
        return _witch_use_poison(state, trigger.command_id, actor, payload, now)
    if isinstance(payload, WitchSkipCommand):
        return _witch_skip(state, trigger.command_id, actor, now)
    if isinstance(payload, SpeakCommand):
        return _speak(state, trigger.command_id, actor, payload, now)
    if isinstance(payload, PassSpeechCommand):
        return _pass_speech(state, trigger.command_id, actor, now)
    if isinstance(payload, VoteCommand):
        return _submit_vote(
            state,
            trigger.command_id,
            actor,
            payload.target_seat_id,
            now,
        )
    if isinstance(payload, AbstainCommand):
        return _submit_vote(
            state,
            trigger.command_id,
            actor,
            None,
            now,
        )
    return _error(state, CommandErrorCode.ILLEGAL_PHASE)


def apply_command(
    state: GameState,
    trigger: CommandEnvelope | SystemTimeout,
    actor: AuthenticatedActor | None,
    now: datetime,
) -> ApplyOutcome:
    state = GameState.revalidate(state)
    if isinstance(trigger, CommandEnvelope):
        trigger = CommandEnvelope.revalidate(trigger)
    elif isinstance(trigger, SystemTimeout):
        trigger = SystemTimeout.revalidate(trigger)
    else:
        raise TypeError("trigger must be a CommandEnvelope or SystemTimeout")
    if actor is not None:
        actor = AuthenticatedActor.revalidate(actor)
    outcome = _apply_command_impl(state, trigger, actor, now)
    next_event_count = state.event_count + len(outcome.events)
    next_event_log_digest = advance_event_log_digest(
        state.event_log_digest,
        outcome.events,
    )
    if (
        outcome.next_state.event_count == next_event_count
        and outcome.next_state.event_log_digest == next_event_log_digest
    ):
        return outcome
    next_state = outcome.next_state.model_copy(
        update={
            "event_count": next_event_count,
            "event_log_digest": next_event_log_digest,
        }
    )
    next_state = _append_public_timeline(next_state, outcome.events)
    return ApplyOutcome(
        next_state=next_state,
        events=outcome.events,
        error_code=outcome.error_code,
    )
