from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tests.conftest import _core_exile_last_wolf, _core_with_wolf_win
from tests.factories import core_at_wolf
from werewolf_dm.application.core import FrozenClock, GameCore
from werewolf_dm.application.dm_contracts import (
    DMAnnouncementSlot,
    TemplateAudience,
    TemplateCatalog,
    TemplateFact,
    TemplateIntent,
)
from werewolf_dm.application.dm_service import TemplateDMService
from werewolf_dm.application.dm_templates import TemplateRegistry
from werewolf_dm.application.rooms import RoomActor
from werewolf_dm.domain.enums import Phase

CATALOG_VERSION = "s4-template-v1"
ROOM_ID = UUID("00000000-0000-0000-0000-000000000001")


class EmptyRegistry(TemplateRegistry):
    def catalog(self, catalog_version: str) -> TemplateCatalog:
        del catalog_version
        raise ValueError("CATALOG_NOT_FOUND")


class RecordingSubscriber:
    def __init__(
        self,
        *,
        actor_type: str,
        seat_id: int | None,
        channels: frozenset[str],
    ) -> None:
        self.subscription_id = uuid4()
        self.actor_type = actor_type
        self.seat_id = seat_id
        self.session_id = None
        self.channels = channels
        self.close_codes: list[int] = []

    def offer(self, message: object) -> bool:
        del message
        return True

    def request_close(self, code: int) -> None:
        self.close_codes.append(code)


def _actor(
    core_: GameCore,
    *,
    dm_service: TemplateDMService | None = None,
    now: datetime | None = None,
) -> RoomActor:
    clock = core_.clock
    sampled_now = clock() if now is None else now
    actor = RoomActor(
        room_id=core_.state.room_id,
        room_code="ROOM01",
        seed=core_.state.seed,
        clock=clock,
        expires_at=sampled_now + timedelta(hours=6),
        last_activity_at=sampled_now,
        core=core_,
        dm_service=dm_service,
    )
    actor.set_monotonic_now(1000)
    return actor


def _slot(
    actor: RoomActor,
    domain_seq: int,
    *,
    revision: int | None = None,
    trigger_at_monotonic_ms: int = 1000,
) -> DMAnnouncementSlot:
    return DMAnnouncementSlot(
        domain_seq=domain_seq,
        room_id=actor.room_id,
        revision=actor.core.state.revision if revision is None else revision,
        trigger_at_monotonic_ms=trigger_at_monotonic_ms,
        admission_deadline_monotonic_ms=trigger_at_monotonic_ms + 2000,
    )


def _current_public_dm_seq(actor: RoomActor) -> int:
    return next(
        item.seq
        for item in reversed(actor.core.state.outbox)
        if item.kind == "dm.message"
        and item.audience_seat_id is None
        and item.revision == actor.core.state.revision
    )


def _game_end_seq(actor: RoomActor) -> int:
    return next(item.seq for item in reversed(actor.core.state.outbox) if item.kind == "game.ended")


def _seat_intent_and_facts(
    actor: RoomActor,
    *,
    domain_seq: int,
    seat_id: int,
    session_id: UUID,
) -> tuple[TemplateIntent, list[TemplateFact]]:
    item = next(item for item in actor.core.state.outbox if item.seq == domain_seq)
    intent = TemplateIntent(
        intent_id=uuid4(),
        source_event_id=item.event_id,
        source_revision=actor.core.state.revision,
        source_phase=Phase.NIGHT_WOLF,
        catalog_version=CATALOG_VERSION,
        category="SEAT_PROMPT",
        channel="seat",
        audience_seat_ids=(seat_id,),
        audience_bindings=(TemplateAudience(seat_id=seat_id, session_id=session_id),),
        template_variant_id="s4-seat-prompt-urgent-v1",
        style="urgent",
        source_event_ids=(item.event_id,),
        allowed_fact_kinds=frozenset({"seat_prompt"}),
    )
    fact = TemplateFact(
        fact_id=uuid4(),
        source_event_id=item.event_id,
        visibility="seat",
        audience_seat_ids=(seat_id,),
        kind="seat_prompt",
        fields={"seat_id": seat_id, "phase_label": "狼人行动"},
    )
    return intent, [fact]


def _public_intent_and_facts(
    actor: RoomActor,
    *,
    domain_seq: int,
) -> tuple[TemplateIntent, list[TemplateFact]]:
    from werewolf_dm.application.dm_intents import (
        project_template_facts,
        select_template_intent,
    )

    item = next(item for item in actor.core.state.outbox if item.seq == domain_seq)
    event = next(event for event in actor.core.events if event.event_id == item.event_id)
    intent = select_template_intent(
        actor.core.state,
        event,
        catalog_version=CATALOG_VERSION,
    )
    return intent, project_template_facts(actor.core.state, event, intent)


@pytest.mark.asyncio
async def test_production_seat_prompts_conserve_domain_and_wire_sequences() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    for seat_id in scenario.wolf_ids:
        await actor.attach_subscriber(
            RecordingSubscriber(
                actor_type="seat",
                seat_id=seat_id,
                channels=frozenset({"public", "seat"}),
            )
        )
    prompt_seqs = [
        item.seq for item in scenario.core.state.outbox if item.audience_seat_id is not None
    ]

    await actor.consume_announcements()

    assert list(actor.domain_to_transport) == prompt_seqs
    assert list(actor.domain_to_transport.values()) == list(range(1, len(prompt_seqs) + 1))
    assert [message.channel for message in actor.published_messages] == ["seat"] * len(prompt_seqs)
    assert actor.client_outbox_seq == len(prompt_seqs)

    await actor.stop()


@pytest.mark.asyncio
async def test_offline_production_seat_prompts_publish_nothing_or_trace() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    prompt_seqs = [
        item.seq for item in scenario.core.state.outbox if item.audience_seat_id is not None
    ]

    await actor.consume_announcements()

    assert prompt_seqs
    assert actor.domain_to_transport == {}
    assert actor.client_outbox_seq == 0
    assert actor.published_messages == []
    assert actor.dm_trace == []
    assert actor.processed_announcement_seq == max(prompt_seqs)


@pytest.mark.asyncio
async def test_game_end_cannot_overtake_earlier_dm_slot() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))

    await actor.consume_announcements()

    assert actor.published_kinds == [
        "dm.message",
        "game.ended",
    ]
    assert actor.client_outbox_seq == 2
    assert [trace.suppress_reason for trace in actor.dm_trace[:2]] == [
        "stale_revision",
        "stale_revision",
    ]
    assert [trace.admission_status for trace in actor.dm_trace[2:]] == [
        "admitted",
        "admitted",
    ]


@pytest.mark.asyncio
async def test_domain_seq_maps_to_client_outbox_seq() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    domain_seq = _current_public_dm_seq(actor)

    result = await actor.consume_slot(domain_seq=domain_seq)

    assert result.admitted is True
    assert actor.domain_to_transport[domain_seq] == 1
    assert actor.client_outbox_seq == 1


@pytest.mark.asyncio
async def test_duplicate_slot_is_not_published_twice() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    domain_seq = _current_public_dm_seq(actor)

    first = await actor.consume_announcement(domain_seq=domain_seq)
    second = await actor.consume_announcement(domain_seq=domain_seq)

    assert first.admitted is True
    assert second.admitted is False
    assert len(actor.published_messages) == 1
    assert actor.last_trace is not None
    assert actor.last_trace.suppress_reason == "duplicate_slot"


@pytest.mark.asyncio
async def test_consume_slot_cannot_overtake_earlier_dm_slot() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))

    result = await actor.consume_slot(domain_seq=_game_end_seq(actor))

    assert result.admitted is True
    assert actor.published_kinds == ["dm.message", "game.ended"]
    assert list(actor.domain_to_transport) == [
        _current_public_dm_seq(actor),
        _game_end_seq(actor),
    ]


@pytest.mark.asyncio
async def test_admission_after_deadline_fails_closed() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    slot = _slot(actor, domain_seq=_current_public_dm_seq(actor))
    actor.set_monotonic_now(3001)

    result = await actor.admit(slot)

    assert result.admitted is False
    assert actor.last_trace is not None
    assert actor.last_trace.suppress_reason == "admission_timeout"
    assert actor.published_messages == []


@pytest.mark.asyncio
async def test_admission_at_deadline_is_allowed() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    slot = _slot(actor, domain_seq=_current_public_dm_seq(actor))
    actor.set_monotonic_now(3000)

    result = await actor.admit(slot)

    assert result.admitted is True
    assert actor.last_trace is not None
    assert actor.last_trace.suppress_reason is None


@pytest.mark.asyncio
async def test_integration_admission_after_original_deadline_fails_closed() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    domain_seq = _current_public_dm_seq(actor)
    event = next(
        event
        for event in actor.core.events
        if event.event_id
        == next(item for item in actor.core.state.outbox if item.seq == domain_seq).event_id
    )
    actor.clock.set(event.created_at + timedelta(seconds=3))
    actor.set_monotonic_now(5000)

    result = await actor.consume_slot(domain_seq=domain_seq)

    assert result.admitted is False
    assert actor.last_trace is not None
    assert actor.last_trace.suppress_reason == "admission_timeout"
    assert actor.published_messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stale", "expected_reason"),
    [
        ("revision", "stale_revision"),
        ("phase", "stale_phase"),
        ("closed", "room_closed"),
    ],
)
async def test_stale_template_admission_is_suppressed(
    stale: str,
    expected_reason: str,
) -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    domain_seq = _current_public_dm_seq(actor)
    intent, facts = _public_intent_and_facts(actor, domain_seq=domain_seq)
    slot = _slot(actor, domain_seq=domain_seq)
    if stale == "revision":
        slot = _slot(actor, domain_seq=domain_seq, revision=actor.core.state.revision + 1)
    elif stale == "phase":
        actor.core._state = actor.core.state.model_copy(update={"phase": Phase.LOBBY})
    else:
        actor.closed = True

    result = await actor.admit(slot, intent=intent, facts=facts)

    assert result.admitted is False
    assert actor.last_trace is not None
    assert actor.last_trace.suppress_reason == expected_reason
    assert actor.published_messages == []


@pytest.mark.asyncio
async def test_render_failure_is_failed_closed() -> None:
    service = TemplateDMService(registry=EmptyRegistry())
    actor = _actor(
        _core_with_wolf_win(poison_good=False),
        dm_service=service,
    )

    await actor.consume_announcements()

    assert actor.published_messages == []
    assert actor.last_trace is not None
    assert actor.last_trace.admission_status == "failed"
    assert actor.last_trace.suppress_reason is None


@pytest.mark.asyncio
async def test_room_closed_is_suppressed() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    domain_seq = _current_public_dm_seq(actor)
    intent, facts = _public_intent_and_facts(actor, domain_seq=domain_seq)
    actor.closed = True

    result = await actor.admit(_slot(actor, domain_seq=domain_seq), intent=intent, facts=facts)

    assert result.admitted is False
    assert actor.last_trace is not None
    assert actor.last_trace.suppress_reason == "room_closed"
    assert actor.published_messages == []


@pytest.mark.asyncio
async def test_seat_session_rebinding_invalidates_old_audience() -> None:
    actor = _actor(_core_exile_last_wolf(), now=datetime(2026, 9, 27, tzinfo=UTC))
    await actor.start()
    first = RecordingSubscriber(
        actor_type="seat",
        seat_id=2,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(first)
    old_session = actor.seat_session_id(2)
    assert old_session is not None
    await actor.publish_current(first)
    assert actor.seat_session_id(2) == old_session

    public_seq = _current_public_dm_seq(actor)
    intent, facts = _seat_intent_and_facts(
        actor,
        domain_seq=public_seq,
        seat_id=2,
        session_id=old_session,
    )
    actor.core._state = actor.core.state.model_copy(update={"phase": Phase.NIGHT_WOLF})
    first_result = await actor.admit(
        _slot(actor, domain_seq=public_seq),
        intent=intent,
        facts=facts,
    )
    assert first_result.admitted is True
    assert len(actor.published_messages) == 1
    assert actor.published_messages[0].channel == "seat"

    await actor.detach_subscriber(first.subscription_id)
    second = RecordingSubscriber(
        actor_type="seat",
        seat_id=2,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(second)
    new_session = actor.seat_session_id(2)
    assert new_session is not None and new_session != old_session

    game_end_seq = _game_end_seq(actor)
    stale_intent, stale_facts = _seat_intent_and_facts(
        actor,
        domain_seq=game_end_seq,
        seat_id=2,
        session_id=old_session,
    )
    stale_result = await actor.admit(
        _slot(actor, domain_seq=game_end_seq),
        intent=stale_intent,
        facts=stale_facts,
    )
    assert stale_result.admitted is False
    assert len(actor.published_messages) == 1

    await actor.stop()


@pytest.mark.asyncio
async def test_public_message_is_not_invalidated_by_seat_rebinding() -> None:
    actor = _actor(_core_exile_last_wolf(), now=datetime(2026, 9, 27, tzinfo=UTC))
    await actor.start()
    first = RecordingSubscriber(
        actor_type="seat",
        seat_id=2,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(first)
    await actor.detach_subscriber(first.subscription_id)
    second = RecordingSubscriber(
        actor_type="seat",
        seat_id=2,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(second)
    domain_seq = _current_public_dm_seq(actor)
    public_intent, public_facts = _public_intent_and_facts(actor, domain_seq=domain_seq)

    public_result = await actor.admit(
        _slot(actor, domain_seq=domain_seq),
        intent=public_intent,
        facts=public_facts,
    )

    assert public_result.admitted is True
    assert actor.published_messages[-1].channel == "public"

    await actor.stop()


@pytest.mark.asyncio
async def test_domain_transport_conservation() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    public_seq = _current_public_dm_seq(actor)
    game_end_seq = _game_end_seq(actor)

    await actor.consume_announcements()

    assert list(actor.domain_to_transport) == [public_seq, game_end_seq]
    assert list(actor.domain_to_transport.values()) == [1, 2]
    assert actor.client_outbox_seq == len(actor.published_messages)


def test_room_actor_accepts_explicit_monotonic_clock() -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)

    def clock() -> int:
        return 1234

    actor = RoomActor(
        room_id=ROOM_ID,
        room_code="ROOM01",
        seed=101,
        clock=FrozenClock(now),
        expires_at=now + timedelta(hours=1),
        last_activity_at=now,
        monotonic_ms=clock,
    )

    actor.set_monotonic_now(4321)

    assert actor.monotonic_now_ms() == 4321
