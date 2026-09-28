from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from tests.conftest import _core_with_wolf_win
from tests.factories import core_at_wolf, envelope_for, seat_actor
from werewolf_dm.application.core import GameCore
from werewolf_dm.application.dm_contracts import (
    DMAnnouncementSlot,
    TemplateAudience,
    TemplateFact,
    TemplateIntent,
)
from werewolf_dm.application.rooms import DMTemplateMessageUpdate, RoomActor
from werewolf_dm.domain.contracts import WolfNominateKillCommand
from werewolf_dm.domain.enums import Phase

CATALOG_VERSION = "s4-template-v1"


class RecordingSubscriber:
    def __init__(
        self,
        *,
        actor_type: str,
        seat_id: int | None,
        channels: frozenset[str],
        accept_dm: bool = True,
    ) -> None:
        self.subscription_id = uuid4()
        self.actor_type = actor_type
        self.seat_id = seat_id
        self.session_id = None
        self.channels = channels
        self.accept_dm = accept_dm
        self.messages: list[object] = []
        self.close_codes: list[int] = []

    def offer(self, message: object) -> bool:
        self.messages.append(message)
        return self.accept_dm if getattr(message, "type", None) == "dm.message" else True

    def request_close(self, code: int) -> None:
        self.close_codes.append(code)


def _actor(core: GameCore) -> RoomActor:
    now = core.clock()
    actor = RoomActor(
        room_id=core.state.room_id,
        room_code="ROOM01",
        seed=core.state.seed,
        clock=core.clock,
        expires_at=now + timedelta(hours=6),
        last_activity_at=now,
        core=core,
    )
    actor.set_monotonic_now(1000)
    return actor


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


def _slot(actor: RoomActor, domain_seq: int) -> DMAnnouncementSlot:
    return DMAnnouncementSlot(
        domain_seq=domain_seq,
        room_id=actor.room_id,
        revision=actor.core.state.revision,
        trigger_at_monotonic_ms=1000,
        admission_deadline_monotonic_ms=3000,
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


@pytest.mark.asyncio
async def test_production_seat_prompt_is_admitted_for_current_session() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    target_seat_id = scenario.wolf_ids[0]
    subscriber = RecordingSubscriber(
        actor_type="seat",
        seat_id=target_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(subscriber)

    await actor.consume_announcements()

    delivered = [message for message in subscriber.messages if message.type == "dm.message"]
    assert len(delivered) == 1
    assert delivered[0].message.channel == "seat"
    assert delivered[0].message.source == "template"
    assert delivered[0].message.audience_bindings[0].seat_id == target_seat_id

    await actor.stop()


@pytest.mark.asyncio
async def test_production_seat_prompt_never_reaches_public_or_other_seat() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    target_seat_id = scenario.wolf_ids[0]
    target = RecordingSubscriber(
        actor_type="seat",
        seat_id=target_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    host = RecordingSubscriber(
        actor_type="host",
        seat_id=None,
        channels=frozenset({"public", "host.control"}),
    )
    stranger = RecordingSubscriber(
        actor_type="seat",
        seat_id=scenario.good_ids[0],
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(target)
    await actor.attach_subscriber(host)
    await actor.attach_subscriber(stranger)

    await actor.consume_announcements()

    assert any(
        message.type == "dm.message" and message.message.channel == "seat"
        for message in target.messages
    )
    assert not any(message.type == "dm.message" for message in host.messages)
    assert not any(message.type == "dm.message" for message in stranger.messages)

    await actor.stop()


@pytest.mark.asyncio
async def test_explicit_seat_intent_must_match_outbox_audience() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    item = next(item for item in scenario.core.state.outbox if item.audience_seat_id is not None)
    assert item.audience_seat_id is not None
    other_seat_id = next(seat_id for seat_id in range(1, 7) if seat_id != item.audience_seat_id)
    for seat_id in (item.audience_seat_id, other_seat_id):
        await actor.attach_subscriber(
            RecordingSubscriber(
                actor_type="seat",
                seat_id=seat_id,
                channels=frozenset({"public", "seat"}),
            )
        )
    session_id = actor.seat_session_id(other_seat_id)
    assert session_id is not None
    intent, facts = _seat_intent_and_facts(
        actor,
        domain_seq=item.seq,
        seat_id=other_seat_id,
        session_id=session_id,
    )

    with pytest.raises(ValueError, match="ANNOUNCEMENT_AUDIENCE_MISMATCH"):
        await actor.admit(_slot(actor, item.seq), intent=intent, facts=facts)

    assert actor.published_messages == []
    assert actor.domain_to_transport == {}
    await actor.stop()


@pytest.mark.asyncio
async def test_explicit_seat_intent_must_match_offline_outbox_audience() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    item = next(item for item in scenario.core.state.outbox if item.audience_seat_id is not None)
    assert item.audience_seat_id is not None
    other_seat_id = next(seat_id for seat_id in range(1, 7) if seat_id != item.audience_seat_id)
    await actor.attach_subscriber(
        RecordingSubscriber(
            actor_type="seat",
            seat_id=other_seat_id,
            channels=frozenset({"public", "seat"}),
        )
    )
    session_id = actor.seat_session_id(other_seat_id)
    assert session_id is not None
    intent, facts = _seat_intent_and_facts(
        actor,
        domain_seq=item.seq,
        seat_id=other_seat_id,
        session_id=session_id,
    )

    with pytest.raises(ValueError, match="ANNOUNCEMENT_AUDIENCE_MISMATCH"):
        await actor.admit(_slot(actor, item.seq), intent=intent, facts=facts)

    assert item.seq not in actor._completed_domain_seqs
    assert actor.dm_trace == []
    assert actor.published_messages == []
    await actor.stop()


@pytest.mark.asyncio
async def test_explicit_intent_requires_an_existing_outbox_item() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    item = next(item for item in scenario.core.state.outbox if item.audience_seat_id is not None)
    assert item.audience_seat_id is not None
    await actor.attach_subscriber(
        RecordingSubscriber(
            actor_type="seat",
            seat_id=item.audience_seat_id,
            channels=frozenset({"public", "seat"}),
        )
    )
    session_id = actor.seat_session_id(item.audience_seat_id)
    assert session_id is not None
    intent, facts = _seat_intent_and_facts(
        actor,
        domain_seq=item.seq,
        seat_id=item.audience_seat_id,
        session_id=session_id,
    )
    missing_seq = max(outbox_item.seq for outbox_item in scenario.core.state.outbox) + 1

    with pytest.raises(ValueError, match="ANNOUNCEMENT_ITEM_NOT_FOUND"):
        await actor.admit(_slot(actor, missing_seq), intent=intent, facts=facts)

    assert missing_seq not in actor._completed_domain_seqs
    assert actor.dm_trace == []
    assert actor.domain_to_transport == {}
    await actor.stop()


@pytest.mark.asyncio
async def test_explicit_intent_rejects_malformed_seat_audience() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    item = next(item for item in scenario.core.state.outbox if item.audience_seat_id is not None)
    assert item.audience_seat_id is not None
    await actor.attach_subscriber(
        RecordingSubscriber(
            actor_type="seat",
            seat_id=item.audience_seat_id,
            channels=frozenset({"public", "seat"}),
        )
    )
    session_id = actor.seat_session_id(item.audience_seat_id)
    assert session_id is not None
    intent, facts = _seat_intent_and_facts(
        actor,
        domain_seq=item.seq,
        seat_id=item.audience_seat_id,
        session_id=session_id,
    )
    payload = intent.model_dump()
    payload["audience_seat_ids"] = ()
    malformed = TemplateIntent.model_construct(**payload)

    with pytest.raises(ValueError, match="ANNOUNCEMENT_AUDIENCE_MISMATCH"):
        await actor.admit(_slot(actor, item.seq), intent=malformed, facts=facts)

    assert item.seq not in actor._completed_domain_seqs
    assert actor.dm_trace == []
    await actor.stop()


@pytest.mark.asyncio
async def test_offline_seat_prompt_is_completed_without_blocking_later_slots() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    offline_seq = max(item.seq for item in scenario.core.state.outbox)

    await actor.consume_announcements()

    assert actor.published_messages == []
    assert actor.dm_trace == []
    assert actor.processed_announcement_seq == offline_seq
    assert {item.seq for item in scenario.core.state.outbox} == actor._completed_domain_seqs

    await actor.start()
    seer = RecordingSubscriber(
        actor_type="seat",
        seat_id=scenario.seer_id,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(seer)
    target = scenario.good_ids[0]
    for wolf_id in scenario.wolf_ids:
        result = scenario.core.submit(
            envelope_for(
                scenario.core,
                wolf_id,
                WolfNominateKillCommand(target_seat_id=target),
            ),
            seat_actor(wolf_id),
        )
        assert result.accepted is True
    assert scenario.core.state.phase is Phase.NIGHT_SEER

    await actor.consume_announcements()

    assert any(
        message.type == "dm.message" and message.message.channel == "seat"
        for message in seer.messages
    )

    await actor.stop()


@pytest.mark.asyncio
async def test_reconnected_seat_uses_new_session_binding() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    target_seat_id = scenario.wolf_ids[0]
    first = RecordingSubscriber(
        actor_type="seat",
        seat_id=target_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(first)
    old_session_id = actor.seat_session_id(target_seat_id)
    assert old_session_id is not None
    await actor.detach_subscriber(first.subscription_id)

    second = RecordingSubscriber(
        actor_type="seat",
        seat_id=target_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(second)
    new_session_id = actor.seat_session_id(target_seat_id)
    assert new_session_id is not None and new_session_id != old_session_id

    await actor.consume_announcements()

    assert not any(message.type == "dm.message" for message in first.messages)
    delivered = [message for message in second.messages if message.type == "dm.message"]
    assert len(delivered) == 1
    assert delivered[0].message.channel == "seat"
    assert delivered[0].message.audience_bindings[0].session_id == new_session_id

    await actor.stop()


@pytest.mark.asyncio
async def test_dm_delivery_uses_the_single_wire_outbox_sequence() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    await actor.start()
    subscriber = RecordingSubscriber(
        actor_type="seat",
        seat_id=2,
        channels=frozenset({"public"}),
    )
    host = RecordingSubscriber(
        actor_type="host",
        seat_id=None,
        channels=frozenset({"public", "host.control"}),
    )
    await actor.attach_subscriber(subscriber)
    await actor.attach_subscriber(host)

    result = await actor.consume_slot(domain_seq=_current_public_dm_seq(actor))

    delivered = [message for message in subscriber.messages if message.type == "dm.message"]
    assert result.admitted is True
    assert actor.outbox_seq == result.transport_seq
    assert actor.client_outbox_seq == actor.outbox_seq
    assert len(delivered) == 1
    assert isinstance(delivered[0], DMTemplateMessageUpdate)
    assert delivered[0].outbox_seq == result.transport_seq
    assert result.message is not None
    assert delivered[0].message.message_id == result.message.message_id
    assert any(message.type == "dm.message" for message in host.messages)

    await actor.stop()


@pytest.mark.asyncio
async def test_mapping_uses_actual_wire_sequence_across_view_updates() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    public_seq = _current_public_dm_seq(actor)
    game_end_seq = _game_end_seq(actor)

    first = await actor.consume_slot(domain_seq=public_seq)
    actor._publish_updates()
    second = await actor.consume_slot(domain_seq=game_end_seq)

    assert first.transport_seq == 1
    assert second.transport_seq == 3
    assert actor.domain_to_transport == {public_seq: 1, game_end_seq: 3}
    assert list(actor.domain_to_transport.values()) == [1, 3]
    assert actor.client_outbox_seq == actor.outbox_seq == 3


@pytest.mark.asyncio
async def test_transport_failure_records_suppressed_trace_without_rollback() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    await actor.start()
    subscriber = RecordingSubscriber(
        actor_type="seat",
        seat_id=2,
        channels=frozenset({"public"}),
        accept_dm=False,
    )
    await actor.attach_subscriber(subscriber)

    current_public_seq = _current_public_dm_seq(actor)
    result = await actor.consume_slot(domain_seq=current_public_seq)

    assert result.admitted is True
    assert actor.domain_to_transport[current_public_seq] == result.transport_seq
    assert len(actor.published_messages) == 1
    assert actor.dm_transport_trace[-1].admission_status == "suppressed"
    assert actor.dm_transport_trace[-1].suppress_reason == "transport_failed"
    next_result = await actor.consume_slot(domain_seq=_game_end_seq(actor))
    assert next_result.admitted is True
    assert len(actor.published_messages) == 2

    await actor.stop()


@pytest.mark.asyncio
async def test_seat_message_is_not_delivered_to_a_different_seat_or_public_subscriber() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    item = next(item for item in scenario.core.state.outbox if item.audience_seat_id is not None)
    assert item.audience_seat_id is not None
    target_seat_id = item.audience_seat_id
    other_seat_id = next(seat_id for seat_id in range(1, 7) if seat_id != target_seat_id)
    seat_two = RecordingSubscriber(
        actor_type="seat",
        seat_id=target_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    host = RecordingSubscriber(
        actor_type="host",
        seat_id=None,
        channels=frozenset({"public", "host.control"}),
    )
    stranger = RecordingSubscriber(
        actor_type="seat",
        seat_id=other_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(seat_two)
    await actor.attach_subscriber(host)
    await actor.attach_subscriber(stranger)
    session_id = actor.seat_session_id(target_seat_id)
    assert session_id is not None
    intent, facts = _seat_intent_and_facts(
        actor,
        domain_seq=item.seq,
        seat_id=target_seat_id,
        session_id=session_id,
    )

    result = await actor.admit(
        _slot(actor, item.seq),
        intent=intent,
        facts=facts,
    )

    assert result.admitted is True
    assert any(
        message.type == "dm.message" and message.message.channel == "seat"
        for message in seat_two.messages
    )
    assert not any(message.type == "dm.message" for message in host.messages)
    assert not any(message.type == "dm.message" for message in stranger.messages)

    await actor.stop()


@pytest.mark.asyncio
async def test_seat_message_rejects_a_stale_same_seat_subscription() -> None:
    scenario = core_at_wolf()
    actor = _actor(scenario.core)
    await actor.start()
    item = next(item for item in scenario.core.state.outbox if item.audience_seat_id is not None)
    assert item.audience_seat_id is not None
    target_seat_id = item.audience_seat_id
    first = RecordingSubscriber(
        actor_type="seat",
        seat_id=target_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(first)
    second = RecordingSubscriber(
        actor_type="seat",
        seat_id=target_seat_id,
        channels=frozenset({"public", "seat"}),
    )
    await actor.attach_subscriber(second)
    session_id = actor.seat_session_id(target_seat_id)
    assert session_id is not None
    actor.subscribers[first.subscription_id] = first
    intent, facts = _seat_intent_and_facts(
        actor,
        domain_seq=item.seq,
        seat_id=target_seat_id,
        session_id=session_id,
    )

    result = await actor.admit(_slot(actor, item.seq), intent=intent, facts=facts)

    assert result.admitted is True
    assert not any(message.type == "dm.message" for message in first.messages)
    assert any(message.type == "dm.message" for message in second.messages)

    await actor.stop()


@pytest.mark.asyncio
async def test_public_dm_is_delivered_to_a_display_subscriber() -> None:
    actor = _actor(_core_with_wolf_win(poison_good=False))
    actor.display_session_id = uuid4()
    await actor.start()
    display = RecordingSubscriber(
        actor_type="display",
        seat_id=None,
        channels=frozenset({"public"}),
    )
    display.session_id = actor.display_session_id
    await actor.attach_subscriber(display)

    result = await actor.consume_slot(domain_seq=_current_public_dm_seq(actor))

    assert result.admitted is True
    assert any(message.type == "dm.message" for message in display.messages)

    await actor.stop()
