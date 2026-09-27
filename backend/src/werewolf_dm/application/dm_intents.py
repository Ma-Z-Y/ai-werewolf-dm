from collections.abc import Mapping
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from werewolf_dm.application.dm_contracts import (
    TemplateAudience,
    TemplateCategory,
    TemplateChannel,
    TemplateFact,
    TemplateFactKind,
    TemplateIntent,
    TemplateStyle,
    TemplateVisibility,
)
from werewolf_dm.domain.contracts import (
    EVENT_PAYLOAD_MODELS,
    DomainEvent,
    EventPayload,
    GameEndedPayload,
    HostPausedPayload,
    HostResumedPayload,
    NoExilePayload,
    PhaseChangedPayload,
    PlayerExiledPayload,
    PlayersDiedPayload,
)
from werewolf_dm.domain.enums import EventType, Phase
from werewolf_dm.domain.model import GameState

_PUBLIC_ROUTES: Mapping[EventType, tuple[str, TemplateStyle, frozenset[str]]] = {
    EventType.PHASE_CHANGED: ("PHASE_NOTICE", "neutral", frozenset({"phase"})),
    EventType.PLAYERS_DIED: ("DEATH_NOTICE", "formal", frozenset({"player_died"})),
    EventType.PLAYER_EXILED: ("EXILE_NOTICE", "formal", frozenset({"player_exiled"})),
    EventType.NO_EXILE: ("NO_EXILE_NOTICE", "neutral", frozenset({"no_exile"})),
    EventType.HOST_PAUSED: ("PAUSE_NOTICE", "urgent", frozenset({"phase"})),
    EventType.HOST_RESUMED: ("PAUSE_NOTICE", "urgent", frozenset({"phase"})),
    EventType.GAME_ENDED: ("TERMINAL_NOTICE", "formal", frozenset({"game_ended"})),
}
_SEAT_PROMPT_PHASES = frozenset(
    {
        Phase.NIGHT_WOLF,
        Phase.NIGHT_SEER,
        Phase.NIGHT_WITCH,
    }
)
_ROUTE_VARIANT_IDS: Mapping[tuple[str, str, str], str] = {
    ("PHASE_NOTICE", "public", "neutral"): "s4-public-phase-neutral-v1",
    ("DEATH_NOTICE", "public", "formal"): "s4-public-death-formal-v1",
    ("EXILE_NOTICE", "public", "formal"): "s4-public-exile-formal-v1",
    ("NO_EXILE_NOTICE", "public", "neutral"): "s4-public-no-exile-neutral-v1",
    ("SEAT_PROMPT", "seat", "urgent"): "s4-seat-prompt-urgent-v1",
    ("PAUSE_NOTICE", "public", "urgent"): "s4-public-pause-urgent-v1",
    ("TERMINAL_NOTICE", "public", "formal"): "s4-public-terminal-formal-v1",
}
_CATEGORY_EVENT_TYPES: Mapping[str, frozenset[EventType]] = {
    "PHASE_NOTICE": frozenset({EventType.PHASE_CHANGED}),
    "DEATH_NOTICE": frozenset({EventType.PLAYERS_DIED}),
    "EXILE_NOTICE": frozenset({EventType.PLAYER_EXILED}),
    "NO_EXILE_NOTICE": frozenset({EventType.NO_EXILE}),
    "SEAT_PROMPT": frozenset({EventType.PHASE_CHANGED}),
    "PAUSE_NOTICE": frozenset(
        {
            EventType.HOST_PAUSED,
            EventType.HOST_RESUMED,
        }
    ),
    "TERMINAL_NOTICE": frozenset({EventType.GAME_ENDED}),
}
_PHASE_LABELS: Mapping[Phase, str] = {
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


def select_template_intent(
    state: GameState,
    event: DomainEvent,
    *,
    catalog_version: str,
    seat_id: int | None = None,
    session_id: UUID | None = None,
    expected_session_id: UUID | None = None,
) -> TemplateIntent:
    if event.room_id != state.room_id or event.revision != state.revision:
        raise ValueError("TEMPLATE_EVENT_MISMATCH")

    payload = _payload_for_event(event)
    if event.event_type is EventType.PHASE_CHANGED:
        if not isinstance(payload, PhaseChangedPayload):
            raise ValueError("TEMPLATE_PAYLOAD_MISMATCH")
        if state.phase is not payload.next_phase:
            raise ValueError("TEMPLATE_EVENT_MISMATCH")
        if seat_id is not None:
            if payload.next_phase not in _SEAT_PROMPT_PHASES:
                raise ValueError("SEAT_PROMPT_NOT_ALLOWED")
            if session_id is None or expected_session_id is None:
                raise ValueError("SEAT_SESSION_REQUIRED")
            if session_id != expected_session_id:
                raise ValueError("SEAT_SESSION_MISMATCH")
            category = "SEAT_PROMPT"
            channel: TemplateChannel = "seat"
            style: TemplateStyle = "urgent"
            allowed_fact_kinds = frozenset({"seat_prompt"})
            source_phase = payload.next_phase
            audience_seat_ids = [seat_id]
            audience_bindings = [
                TemplateAudience(seat_id=seat_id, session_id=session_id),
            ]
        else:
            if session_id is not None or expected_session_id is not None:
                raise ValueError("SEAT_PROMPT_NOT_ALLOWED")
            if event.visibility.scope != "public":
                raise ValueError("TEMPLATE_VISIBILITY_MISMATCH")
            category, style, allowed_fact_kinds = _PUBLIC_ROUTES[event.event_type]
            channel = "public"
            source_phase = payload.next_phase
            audience_seat_ids = []
            audience_bindings = []
    else:
        if seat_id is not None or session_id is not None or expected_session_id is not None:
            raise ValueError("SEAT_PROMPT_NOT_ALLOWED")
        route = _PUBLIC_ROUTES.get(event.event_type)
        if route is None:
            raise ValueError("NO_TEMPLATE_INTENT")
        if event.visibility.scope != "public":
            raise ValueError("TEMPLATE_VISIBILITY_MISMATCH")
        category, style, allowed_fact_kinds = route
        channel = "public"
        source_phase = state.phase
        audience_seat_ids = []
        audience_bindings = []

    variant_id = _ROUTE_VARIANT_IDS[(category, channel, style)]
    intent_id = uuid5(
        NAMESPACE_URL,
        f"{event.event_id}:{category}:{channel}:{seat_id or 'public'}",
    )
    return TemplateIntent(
        intent_id=intent_id,
        source_event_id=event.event_id,
        source_revision=event.revision,
        source_phase=source_phase,
        catalog_version=catalog_version,
        category=cast(TemplateCategory, category),
        channel=channel,
        audience_seat_ids=tuple(audience_seat_ids),
        audience_bindings=tuple(audience_bindings),
        template_variant_id=variant_id,
        style=style,
        source_event_ids=(event.event_id,),
        allowed_fact_kinds=allowed_fact_kinds,
    )


def project_template_facts(
    state: GameState,
    event: DomainEvent,
    intent: TemplateIntent,
) -> list[TemplateFact]:
    if (
        event.event_id != intent.source_event_id
        or event.room_id != state.room_id
        or event.revision != state.revision
        or event.event_type not in _CATEGORY_EVENT_TYPES[intent.category]
    ):
        raise ValueError("TEMPLATE_EVENT_MISMATCH")
    payload = _payload_for_event(event)
    if isinstance(payload, PhaseChangedPayload) and state.phase is not payload.next_phase:
        raise ValueError("TEMPLATE_EVENT_MISMATCH")
    fact_kind, fields = _project_fact_fields(payload, intent, state)
    if fact_kind not in intent.allowed_fact_kinds:
        raise ValueError("FACT_NOT_ALLOWED")
    visibility: TemplateVisibility = "seat" if intent.channel == "seat" else "public"
    audience_seat_ids = tuple(intent.audience_seat_ids)
    fact = TemplateFact(
        fact_id=uuid5(NAMESPACE_URL, f"{event.event_id}:{fact_kind}:1"),
        source_event_id=event.event_id,
        visibility=visibility,
        audience_seat_ids=audience_seat_ids,
        kind=fact_kind,
        fields=fields,
    )
    return [fact]


def _payload_for_event(event: DomainEvent) -> EventPayload:
    return EVENT_PAYLOAD_MODELS[event.event_type].validate_json_payload(event.fact_payload)


def _project_fact_fields(
    payload: EventPayload,
    intent: TemplateIntent,
    state: GameState,
) -> tuple[TemplateFactKind, dict[str, str | int]]:
    if intent.category == "PHASE_NOTICE":
        if not isinstance(payload, PhaseChangedPayload):
            raise ValueError("TEMPLATE_PAYLOAD_MISMATCH")
        return "phase", {
            "phase_label": _PHASE_LABELS[payload.next_phase],
            "day": payload.day,
        }
    if intent.category == "DEATH_NOTICE":
        if not isinstance(payload, PlayersDiedPayload):
            raise ValueError("TEMPLATE_PAYLOAD_MISMATCH")
        return "player_died", {
            "seat_ids": ",".join(str(seat_id) for seat_id in payload.seat_ids),
            "count": len(payload.seat_ids),
        }
    if intent.category == "EXILE_NOTICE":
        if not isinstance(payload, PlayerExiledPayload):
            raise ValueError("TEMPLATE_PAYLOAD_MISMATCH")
        return "player_exiled", {"seat_id": payload.seat_id}
    if intent.category == "NO_EXILE_NOTICE":
        if not isinstance(payload, NoExilePayload):
            raise ValueError("TEMPLATE_PAYLOAD_MISMATCH")
        return "no_exile", {"reason_label": _NO_EXILE_LABELS[payload.reason]}
    if intent.category == "SEAT_PROMPT":
        if len(intent.audience_seat_ids) != 1:
            raise ValueError("SEAT_PROMPT_AUDIENCE_MISMATCH")
        if not isinstance(payload, PhaseChangedPayload):
            raise ValueError("TEMPLATE_PAYLOAD_MISMATCH")
        if payload.next_phase not in _SEAT_PROMPT_PHASES:
            raise ValueError("SEAT_PROMPT_NOT_ALLOWED")
        return "seat_prompt", {
            "seat_id": intent.audience_seat_ids[0],
            "phase_label": _PHASE_LABELS[payload.next_phase],
        }
    if intent.category == "PAUSE_NOTICE":
        state_label = "暂停" if isinstance(payload, HostPausedPayload) else "恢复"
        if not isinstance(payload, (HostPausedPayload, HostResumedPayload)):
            raise ValueError("PAUSE_PAYLOAD_MISMATCH")
        return "phase", {
            "phase_label": state_label,
            "day": state.day,
        }
    if intent.category == "TERMINAL_NOTICE":
        if not isinstance(payload, GameEndedPayload):
            raise ValueError("TEMPLATE_PAYLOAD_MISMATCH")
        winner_label = "好人阵营" if payload.winner.value == "GOOD" else "狼人阵营"
        return "game_ended", {"winner_label": winner_label}
    raise ValueError("NO_TEMPLATE_INTENT")


_NO_EXILE_LABELS: Mapping[str, str] = {
    "NO_VOTES": "无人投票",
    "PK_TIE": "PK 平票",
    "NO_VALID_PK_VOTES": "PK 无有效票",
}
