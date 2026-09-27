from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from werewolf_dm.application.dm_contracts import TemplateIntent
from werewolf_dm.application.dm_intents import (
    project_template_facts,
    select_template_intent,
)
from werewolf_dm.domain.contracts import (
    GameEndedPayload,
    HostPausedPayload,
    NoExilePayload,
    PhaseChangedPayload,
    PlayerExiledPayload,
    PlayersDiedPayload,
    PublicVisibility,
    SpeechRecordedPayload,
    build_event,
)
from werewolf_dm.domain.enums import EventType, Faction, Phase
from werewolf_dm.domain.model import GameState

ROOM_ID = UUID("00000000-0000-0000-0000-000000000001")
SESSION_ID = UUID("00000000-0000-0000-0000-000000000201")
CATALOG_VERSION = "s4-template-v1"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _state(*, phase: Phase = Phase.DAY_ANNOUNCE, revision: int = 3) -> GameState:
    return GameState(
        room_id=ROOM_ID,
        seed=101,
        revision=revision,
        phase=phase,
        day=1,
    )


def _event(
    event_type: EventType,
    payload,
    *,
    ordinal: int = 1,
    revision: int = 3,
    visibility=None,
):
    return build_event(
        cause_id=UUID("00000000-0000-0000-0000-000000000301"),
        event_ordinal=ordinal,
        room_id=ROOM_ID,
        revision=revision,
        event_type=event_type,
        visibility=visibility or PublicVisibility(),
        payload=payload,
        created_at=NOW,
    )


def _players_died_event():
    return _event(
        EventType.PLAYERS_DIED,
        PlayersDiedPayload(seat_ids=(2, 4)),
    )


def _seat_prompt_event():
    return _event(
        EventType.PHASE_CHANGED,
        PhaseChangedPayload(
            previous_phase=Phase.NIGHT_SEER,
            next_phase=Phase.NIGHT_WITCH,
            day=1,
        ),
    )


def _select(state: GameState, event, **kwargs):
    return select_template_intent(
        state,
        event,
        catalog_version=CATALOG_VERSION,
        **kwargs,
    )


def test_all_active_intents_are_template_only() -> None:
    intent = _select(_state(), _players_died_event())

    assert intent.source == "template"
    assert intent.catalog_version == CATALOG_VERSION
    assert intent.category == "DEATH_NOTICE"
    assert intent.channel == "public"


def test_seat_intent_never_contains_provider_fields() -> None:
    state = _state(phase=Phase.NIGHT_WITCH, revision=3)
    intent = _select(
        state,
        _seat_prompt_event(),
        seat_id=2,
        session_id=SESSION_ID,
        expected_session_id=SESSION_ID,
    )
    dumped = intent.model_dump()

    assert intent.category == "SEAT_PROMPT"
    assert intent.channel == "seat"
    assert intent.audience_seat_ids == (2,)
    assert intent.audience_bindings[0].session_id == SESSION_ID
    assert "llm_eligible" not in dumped
    assert "provider" not in dumped


@pytest.mark.parametrize(
    ("event_type", "payload", "phase", "expected_category", "expected_channel"),
    [
        (
            EventType.PHASE_CHANGED,
            PhaseChangedPayload(
                previous_phase=Phase.NIGHT_RESOLVE,
                next_phase=Phase.DAY_ANNOUNCE,
                day=1,
            ),
            Phase.DAY_ANNOUNCE,
            "PHASE_NOTICE",
            "public",
        ),
        (
            EventType.PLAYER_EXILED,
            PlayerExiledPayload(seat_id=3),
            Phase.DAY_EXILE,
            "EXILE_NOTICE",
            "public",
        ),
        (
            EventType.NO_EXILE,
            NoExilePayload(reason="PK_TIE"),
            Phase.DAY_EXILE,
            "NO_EXILE_NOTICE",
            "public",
        ),
        (
            EventType.HOST_PAUSED,
            HostPausedPayload(reason="host pause"),
            Phase.DAY_DISCUSSION,
            "PAUSE_NOTICE",
            "public",
        ),
        (
            EventType.GAME_ENDED,
            GameEndedPayload(winner=Faction.GOOD),
            Phase.GAME_END,
            "TERMINAL_NOTICE",
            "public",
        ),
    ],
)
def test_public_route_table_is_hardcoded(
    event_type: EventType,
    payload,
    phase: Phase,
    expected_category: str,
    expected_channel: str,
) -> None:
    event = _event(event_type, payload)
    intent = _select(_state(phase=phase), event)

    assert intent.category == expected_category
    assert intent.channel == expected_channel


def test_player_speech_never_produces_template_intent() -> None:
    event = _event(
        EventType.SPEECH_RECORDED,
        SpeechRecordedPayload(seat_id=2, text="我不是狼人"),
    )

    with pytest.raises(ValueError, match="NO_TEMPLATE_INTENT"):
        _select(_state(phase=Phase.DAY_DISCUSSION), event)


def test_fact_projection_returns_only_allowlisted_facts() -> None:
    event = _players_died_event()
    intent = _select(_state(), event)
    facts = project_template_facts(_state(), event, intent)

    assert facts
    assert all(fact.kind in intent.allowed_fact_kinds for fact in facts)
    assert all(fact.source_event_id == intent.source_event_id for fact in facts)
    assert facts[0].fact_id == uuid5(NAMESPACE_URL, f"{event.event_id}:player_died:1")
    assert facts[0].fields["seat_ids"] == "2,4"
    assert "cause" not in facts[0].fields


def test_fact_projection_rejects_event_mismatch_and_raw_speech() -> None:
    death_event = _players_died_event()
    intent = _select(_state(), death_event)
    speech_event = _event(
        EventType.SPEECH_RECORDED,
        SpeechRecordedPayload(seat_id=2, text="我是预言家"),
    )

    with pytest.raises(ValueError, match="TEMPLATE_EVENT_MISMATCH"):
        project_template_facts(_state(), speech_event, intent)


def test_fact_projection_rejects_category_payload_mismatch() -> None:
    event = _event(
        EventType.PHASE_CHANGED,
        PhaseChangedPayload(
            previous_phase=Phase.NIGHT_RESOLVE,
            next_phase=Phase.DAY_ANNOUNCE,
            day=1,
        ),
    )
    intent = _select(_state(), event)
    mismatched = intent.model_copy(
        update={
            "category": "DEATH_NOTICE",
            "allowed_fact_kinds": frozenset({"player_died"}),
            "template_variant_id": "s4-public-death-formal-v1",
            "style": "formal",
        }
    )

    with pytest.raises(ValueError, match="TEMPLATE_EVENT_MISMATCH"):
        project_template_facts(_state(), event, mismatched)


def test_seat_prompt_projection_rejects_stale_state_phase() -> None:
    event = _seat_prompt_event()
    intent = _select(
        _state(phase=Phase.NIGHT_WITCH),
        event,
        seat_id=2,
        session_id=SESSION_ID,
        expected_session_id=SESSION_ID,
    )

    with pytest.raises(ValueError, match="TEMPLATE_EVENT_MISMATCH"):
        project_template_facts(_state(phase=Phase.DAY_VOTE), event, intent)


def test_seat_prompt_requires_current_session_binding() -> None:
    state = _state(phase=Phase.NIGHT_WITCH)
    event = _seat_prompt_event()

    with pytest.raises(ValueError, match="SEAT_SESSION_REQUIRED"):
        _select(state, event, seat_id=2)
    with pytest.raises(ValueError, match="SEAT_SESSION_REQUIRED"):
        _select(
            state,
            event,
            seat_id=2,
            session_id=SESSION_ID,
        )
    with pytest.raises(ValueError, match="SEAT_SESSION_MISMATCH"):
        _select(
            state,
            event,
            seat_id=2,
            session_id=SESSION_ID,
            expected_session_id=UUID("00000000-0000-0000-0000-000000000202"),
        )


def test_public_phase_rejects_session_arguments() -> None:
    state = _state()
    event = _event(
        EventType.PHASE_CHANGED,
        PhaseChangedPayload(
            previous_phase=Phase.NIGHT_RESOLVE,
            next_phase=Phase.DAY_ANNOUNCE,
            day=1,
        ),
    )

    with pytest.raises(ValueError, match="SEAT_PROMPT_NOT_ALLOWED"):
        _select(
            state,
            event,
            session_id=SESSION_ID,
            expected_session_id=SESSION_ID,
        )


def test_template_intent_is_deterministic() -> None:
    state = _state()
    event = _players_died_event()

    first = _select(state, event)
    second = _select(state, event)

    assert isinstance(first, TemplateIntent)
    assert first == second
