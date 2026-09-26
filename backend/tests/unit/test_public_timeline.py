from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.factories import core_at_day_vote
from werewolf_dm.domain.contracts import (
    GameEndedPayload,
    HostPausedPayload,
    HostResumedPayload,
    NoExilePayload,
    PhaseChangedPayload,
    PlayerExiledPayload,
    PlayersDiedPayload,
    PublicVisibility,
    ReadyChangedPayload,
    RoleAssignedPayload,
    RoleConfirmedPayload,
    RoomJoinedPayload,
    SeatVisibility,
    SpeechPassedPayload,
    SpeechRecordedPayload,
    TimeoutAppliedPayload,
    VoteRoundResolvedPayload,
    build_event,
)
from werewolf_dm.domain.enums import EventType, Faction, Phase, Role
from werewolf_dm.domain.replay import state_hash
from werewolf_dm.domain.state_machine import (
    PHASE_STATEMENT_LABELS,
    _append_public_timeline,
    _public_statement,
    initial_state,
)


def public_event(room_id, event_type, payload, *, revision=0):
    return build_event(
        cause_id=uuid4(),
        event_ordinal=1,
        room_id=room_id,
        revision=revision,
        event_type=event_type,
        visibility=PublicVisibility(),
        payload=payload,
        created_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


def test_public_timeline_contains_only_public_events():
    core = core_at_day_vote()
    expected = tuple(
        (event.event_id, event.revision, event.event_type)
        for event in core.events
        if event.visibility.scope == "public"
    )
    actual = tuple(
        (item.event_id, item.revision, item.event_type) for item in core.state.public_timeline
    )

    assert core.state.public_timeline
    assert actual == expected
    assert len(actual) == len(set(actual))


def test_public_timeline_excludes_private_event_ids_and_facts():
    core = core_at_day_vote()
    public_event_ids = {
        event.event_id for event in core.events if event.visibility.scope == "public"
    }
    private_event_ids = {
        event.event_id for event in core.events if event.visibility.scope != "public"
    }

    assert {item.event_id for item in core.state.public_timeline} == public_event_ids
    assert private_event_ids.isdisjoint(item.event_id for item in core.state.public_timeline)
    statements = " ".join(item.statement for item in core.state.public_timeline)
    for secret in (
        "WEREWOLF",
        "SEER",
        "WITCH",
        "WOLF",
        "POISON",
        "ANTIDOTE",
        "target_seat_id",
        "cause",
    ):
        assert secret not in statements


def test_vote_resolution_and_exile_have_distinct_public_statements():
    room_id = uuid4()
    resolved = _public_statement(
        public_event(
            room_id,
            EventType.VOTE_ROUND_RESOLVED,
            VoteRoundResolvedPayload(
                round_id=uuid4(),
                tallies=(),
                exiled_seat_id=3,
                tie=False,
            ),
        )
    )
    exiled = _public_statement(
        public_event(
            room_id,
            EventType.PLAYER_EXILED,
            PlayerExiledPayload(seat_id=3),
        )
    )

    assert resolved == "投票结果：3 号玩家得票最多"  # noqa: RUF001
    assert exiled == "3 号玩家被放逐"
    assert resolved != exiled


def test_game_end_statement_contains_winner_without_private_roles():
    room_id = uuid4()
    state = initial_state(room_id=room_id, seed=1001)
    event = public_event(
        room_id,
        EventType.GAME_ENDED,
        GameEndedPayload(winner=Faction.GOOD),
    )

    state = _append_public_timeline(state, [event])
    final = state.public_timeline[-1]

    assert final.event_type.value == "GAME_ENDED"
    assert "好人阵营获胜" in final.statement
    for secret in ("预言家", "女巫", "WOLF", "POISON", "WITCH"):
        assert secret not in final.statement


def test_public_timeline_not_in_state_hash_or_event_log_digest():
    core = core_at_day_vote()
    before_hash = state_hash(core.state)
    before_digest = core.state.event_log_digest
    changed = core.state.model_copy(
        update={
            "public_timeline": tuple(reversed(core.state.public_timeline)),
        }
    )

    assert state_hash(changed) == before_hash
    assert changed.event_log_digest == before_digest


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        (
            EventType.PHASE_CHANGED,
            PhaseChangedPayload(
                previous_phase=Phase.NIGHT_RESOLVE,
                next_phase=Phase.DAY_VOTE,
                day=2,
            ),
            "进入第 2 天 · 白天投票",
        ),
        (
            EventType.PLAYERS_DIED,
            PlayersDiedPayload(seat_ids=()),
            "昨夜平安",
        ),
        (
            EventType.PLAYERS_DIED,
            PlayersDiedPayload(seat_ids=(2, 4)),
            "2 号、4 号 玩家出局",
        ),
        (
            EventType.PLAYER_EXILED,
            PlayerExiledPayload(seat_id=3),
            "3 号玩家被放逐",
        ),
        (
            EventType.NO_EXILE,
            NoExilePayload(reason="PK_TIE"),
            "本轮无人出局",
        ),
        (
            EventType.VOTE_ROUND_RESOLVED,
            VoteRoundResolvedPayload(
                round_id=uuid4(),
                tallies=(),
                exiled_seat_id=None,
                tie=True,
            ),
            "投票平票",
        ),
        (
            EventType.TIMEOUT_APPLIED,
            TimeoutAppliedPayload(phase=Phase.DAY_VOTE, timeout_reason="VOTE"),
            "白天投票阶段超时，系统自动推进",  # noqa: RUF001
        ),
        (
            EventType.HOST_PAUSED,
            HostPausedPayload(reason="主持人暂停"),
            "游戏已暂停",
        ),
        (
            EventType.HOST_RESUMED,
            HostResumedPayload(),
            "游戏已恢复",
        ),
        (
            EventType.GAME_ENDED,
            GameEndedPayload(winner=Faction.WEREWOLF),
            "游戏结束：狼人阵营获胜",  # noqa: RUF001
        ),
    ],
)
def test_public_statement_has_chinese_templates(event_type, payload, expected):
    event = public_event(uuid4(), event_type, payload)

    assert _public_statement(event) == expected


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        (EventType.ROOM_JOINED, RoomJoinedPayload(seat_id=1, display_name="玩家"), "1 号玩家加入"),
        (EventType.READY_CHANGED, ReadyChangedPayload(seat_id=2, ready=True), "2 号玩家准备"),
        (
            EventType.READY_CHANGED,
            ReadyChangedPayload(seat_id=2, ready=False),
            "2 号玩家取消准备",
        ),
        (EventType.ROLE_CONFIRMED, RoleConfirmedPayload(seat_id=3), "3 号玩家已确认角色"),
        (
            EventType.SPEECH_RECORDED,
            SpeechRecordedPayload(seat_id=4, text="公开发言"),
            "4 号玩家发言结束",
        ),
        (
            EventType.SPEECH_PASSED,
            SpeechPassedPayload(seat_id=5, timed_out=False),
            "5 号玩家跳过发言",
        ),
    ],
)
def test_public_statement_covers_all_public_event_types(event_type, payload, expected):
    event = public_event(uuid4(), event_type, payload)

    assert _public_statement(event) == expected


def test_phase_statement_label_mapping_is_exhaustive():
    assert set(PHASE_STATEMENT_LABELS) == set(Phase)


@pytest.mark.parametrize("phase", list(Phase))
def test_public_statement_never_exposes_phase_enum(phase):
    event = public_event(
        uuid4(),
        EventType.TIMEOUT_APPLIED,
        TimeoutAppliedPayload(phase=phase, timeout_reason="VOTE"),
    )
    statement = _public_statement(event)

    assert PHASE_STATEMENT_LABELS[phase] in statement
    assert phase.value not in statement


def test_timeline_statement_length_metadata_and_public_only_projection():
    room_id = uuid4()
    state = initial_state(room_id=room_id, seed=1001)
    public = public_event(
        room_id,
        EventType.PLAYERS_DIED,
        PlayersDiedPayload(seat_ids=(1, 2, 3, 4, 5, 6)),
        revision=17,
    )
    private = build_event(
        cause_id=uuid4(),
        event_ordinal=2,
        room_id=room_id,
        revision=17,
        event_type=EventType.ROLE_ASSIGNED,
        visibility=SeatVisibility(seat_id=1),
        payload=RoleAssignedPayload(seat_id=1, role=Role.WEREWOLF),
        created_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    state = _append_public_timeline(state, [public, private])

    assert len(state.public_timeline) == 1
    item = state.public_timeline[0]
    assert item.event_id == public.event_id
    assert item.revision == public.revision
    assert item.event_type is public.event_type
    assert len(item.statement) <= 240
