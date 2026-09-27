from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from werewolf_dm.application.dm_contracts import (
    DMAdmissionResult,
    DMAnnouncementSlot,
    DMTemplateMessage,
    DMTraceRecord,
    TemplateAudience,
    TemplateCatalog,
    TemplateFact,
    TemplateIntent,
    TemplateRenderRequest,
    TemplateRenderResult,
    TemplateVariantKey,
)
from werewolf_dm.application.dm_templates import TemplateRegistry
from werewolf_dm.domain.enums import Phase

ROOM_ID = UUID("00000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000101")
SESSION_ID = UUID("00000000-0000-0000-0000-000000000201")


def _public_intent() -> TemplateIntent:
    return TemplateIntent(
        intent_id=UUID("00000000-0000-0000-0000-000000000301"),
        source_event_id=EVENT_ID,
        source_revision=3,
        source_phase=Phase.DAY_ANNOUNCE,
        catalog_version="s4-template-v1",
        category="DEATH_NOTICE",
        channel="public",
        audience_seat_ids=(),
        audience_bindings=(),
        template_variant_id="s4-public-death-formal-v1",
        style="formal",
        source_event_ids=(EVENT_ID,),
        allowed_fact_kinds=frozenset({"player_died"}),
    )


def test_template_intent_rejects_extra_fields_and_provider_keys() -> None:
    payload = _public_intent().model_dump()
    payload["llm_eligible"] = True

    with pytest.raises(ValidationError):
        TemplateIntent(**payload)

    assert "llm_eligible" not in _public_intent().model_dump()
    assert "provider" not in _public_intent().model_dump()


def test_template_intent_sorts_and_deduplicates_uuid_lists() -> None:
    second_event = UUID("00000000-0000-0000-0000-000000000102")
    intent = _public_intent().model_copy(
        update={"source_event_ids": (second_event, EVENT_ID, EVENT_ID)}
    )

    assert intent.source_event_ids == (EVENT_ID, second_event)


def test_validated_contract_containers_are_deeply_immutable() -> None:
    intent = _public_intent()
    with pytest.raises(TypeError):
        list.append(
            intent.source_event_ids,
            UUID("00000000-0000-0000-0000-000000000102"),
        )
    assert intent.source_event_ids == (EVENT_ID,)

    fact = TemplateFact(
        fact_id=uuid4(),
        source_event_id=EVENT_ID,
        visibility="public",
        audience_seat_ids=(),
        kind="player_died",
        fields={"seat_ids": "2", "count": 1},
    )
    with pytest.raises(TypeError):
        fact.fields["hidden_role"] = "WEREWOLF"
    with pytest.raises(TypeError):
        list.append(fact.audience_seat_ids, 2)
    fact.fields.__init__({"hidden_role": "WEREWOLF"})
    with pytest.raises(TypeError):
        dict.__setitem__(fact.fields, "hidden_role", "WEREWOLF")
    with pytest.raises((TypeError, ValidationError)):
        fact.fields |= {"hidden_role": "WEREWOLF"}
    assert fact.audience_seat_ids == ()
    assert dict(fact.fields) == {"seat_ids": "2", "count": 1}


def test_template_audience_requires_consistent_public_or_seat_shape() -> None:
    assert TemplateAudience(seat_id=None, session_id=None).seat_id is None
    with pytest.raises(ValidationError):
        TemplateAudience(seat_id=2, session_id=None)
    with pytest.raises(ValidationError):
        TemplateAudience(seat_id=None, session_id=SESSION_ID)


def test_template_intent_seat_channel_binds_exactly_one_current_session() -> None:
    audience = TemplateAudience(seat_id=2, session_id=SESSION_ID)
    intent = TemplateIntent(
        intent_id=uuid4(),
        source_event_id=EVENT_ID,
        source_revision=4,
        source_phase=Phase.NIGHT_WITCH,
        catalog_version="s4-template-v1",
        category="SEAT_PROMPT",
        channel="seat",
        audience_seat_ids=(2,),
        audience_bindings=(audience,),
        template_variant_id="s4-seat-prompt-urgent-v1",
        style="urgent",
        source_event_ids=(EVENT_ID,),
        allowed_fact_kinds=frozenset({"seat_prompt"}),
    )

    assert intent.audience_bindings == (audience,)
    with pytest.raises(ValidationError):
        intent.model_copy(update={"audience_bindings": ()})
    with pytest.raises(ValidationError):
        intent.model_copy(
            update={
                "audience_bindings": (
                    audience,
                    TemplateAudience(
                        seat_id=3,
                        session_id=UUID("00000000-0000-0000-0000-000000000202"),
                    ),
                )
            }
        )


def test_template_fact_rejects_unknown_fields_and_unsorted_audience() -> None:
    with pytest.raises(ValidationError):
        TemplateFact(
            fact_id=uuid4(),
            source_event_id=EVENT_ID,
            visibility="public",
            audience_seat_ids=(),
            kind="player_died",
            fields={"seat_ids": "2,3", "cause": "WOLF"},
        )

    fact = TemplateFact(
        fact_id=uuid4(),
        source_event_id=EVENT_ID,
        visibility="seat",
        audience_seat_ids=(2, 2),
        kind="seat_prompt",
        fields={"seat_id": 2, "phase_label": "女巫行动"},
    )
    assert fact.audience_seat_ids == (2,)


def test_template_variant_key_is_strict_catalog_versioned_and_immutable() -> None:
    key = TemplateVariantKey(
        catalog_version="s4-template-v1",
        category="DEATH_NOTICE",
        channel="public",
        style="formal",
        audience_bindings=(),
        source_event_id=EVENT_ID,
    )

    assert key.catalog_version == "s4-template-v1"
    with pytest.raises(TypeError):
        list.append(key.audience_bindings, TemplateAudience())
    with pytest.raises(ValidationError):
        TemplateVariantKey(
            **{**key.model_dump(), "provider": "forbidden"},
        )
    with pytest.raises(ValidationError):
        TemplateVariantKey(
            catalog_version="s4-template-v1",
            category="SEAT_PROMPT",
            channel="seat",
            style="urgent",
            audience_bindings=(TemplateAudience(),),
            source_event_id=EVENT_ID,
        )


def test_template_catalog_preserves_reserved_compatibility_fields() -> None:
    base = TemplateRegistry().catalog("s4-template-v1")
    variant_id = "s4-public-phase-neutral-v1"
    variant = base.variants[variant_id].model_copy(
        update={"deprecated_at_version": "s4-template-v2"}
    )
    catalog = base.model_copy(
        update={
            "variants": {**base.variants, variant_id: variant},
            "reserved_variant_ids": frozenset({"s4-reserved-v1"}),
        }
    )

    dumped = catalog.model_dump(mode="json")
    assert dumped["variants"][variant.template_variant_id]["deprecated_at_version"] == (
        "s4-template-v2"
    )
    assert dumped["reserved_variant_ids"] == ["s4-reserved-v1"]


def test_template_catalog_rejects_duplicate_variant_ids() -> None:
    variant = TemplateRegistry().catalog("s4-template-v1").variants["s4-public-phase-neutral-v1"]

    with pytest.raises(ValidationError):
        TemplateCatalog(
            catalog_version="s4-template-v1",
            variants={
                "first": variant,
                "second": variant,
            },
        )


def test_render_and_transport_contracts_keep_template_only_shape() -> None:
    intent = _public_intent()
    request = TemplateRenderRequest(
        intent=intent,
        catalog_version="s4-template-v1",
        facts=(),
    )
    result = TemplateRenderResult(
        intent_id=intent.intent_id,
        template_variant_id=intent.template_variant_id,
        catalog_version=request.catalog_version,
        final_text="昨夜，2 号玩家出局。",  # noqa: RUF001
        source_event_ids=(EVENT_ID,),
        unused_fact_ids=(),
        channel="public",
        audience_bindings=(),
    )

    assert result.source == "template"
    assert "provider" not in result.model_dump()
    with pytest.raises(ValidationError):
        TemplateRenderResult(
            intent_id=intent.intent_id,
            template_variant_id="s4-seat-prompt-urgent-v1",
            catalog_version=request.catalog_version,
            final_text="请 2 号玩家行动。",
            source_event_ids=(EVENT_ID,),
            unused_fact_ids=(),
            channel="seat",
            audience_bindings=(TemplateAudience(),),
        )


def test_transport_message_requires_one_seat_binding() -> None:
    first = TemplateAudience(seat_id=2, session_id=SESSION_ID)
    message = DMTemplateMessage(
        message_id=uuid4(),
        room_id=ROOM_ID,
        revision=3,
        channel="seat",
        audience_bindings=(first,),
        text="请行动。",
        source="template",
    )

    assert message.audience_bindings == (first,)
    with pytest.raises(TypeError):
        list.append(message.audience_bindings, first)
    with pytest.raises(ValidationError):
        message.model_copy(
            update={
                "audience_bindings": (
                    first,
                    TemplateAudience(
                        seat_id=3,
                        session_id=UUID("00000000-0000-0000-0000-000000000203"),
                    ),
                )
            }
        )
    with pytest.raises(ValidationError):
        DMTemplateMessage(
            message_id=uuid4(),
            room_id=ROOM_ID,
            revision=3,
            channel="seat",
            audience_bindings=(TemplateAudience(),),
            text="请行动。",
            source="template",
        )


def test_trace_suppress_reason_is_optional_and_status_bound() -> None:
    base = {
        "trace_id": uuid4(),
        "intent_id": uuid4(),
        "template_variant_id": "s4-public-death-formal-v1",
        "catalog_version": "s4-template-v1",
        "source_event_ids": (EVENT_ID,),
        "channel": "public",
        "audience_seat_ids": (),
        "elapsed_ms": 1,
    }

    assert DMTraceRecord(**base, admission_status="admitted").suppress_reason is None
    assert DMTraceRecord(**base, admission_status="failed").suppress_reason is None
    assert (
        DMTraceRecord(
            **base,
            admission_status="suppressed",
            suppress_reason="duplicate_slot",
        ).suppress_reason
        == "duplicate_slot"
    )

    with pytest.raises(ValidationError):
        DMTraceRecord(**base, admission_status="admitted", suppress_reason="duplicate_slot")
    with pytest.raises(ValidationError):
        DMTraceRecord(**base, admission_status="suppressed")

    with pytest.raises(ValidationError):
        DMTraceRecord(
            **{**base, "channel": "seat"},
            admission_status="admitted",
        )


def test_announcement_slot_enforces_exact_two_second_deadline() -> None:
    slot = DMAnnouncementSlot(
        domain_seq=1,
        room_id=ROOM_ID,
        revision=3,
        trigger_at_monotonic_ms=1000,
        admission_deadline_monotonic_ms=3000,
    )

    assert slot.admission_deadline_monotonic_ms == slot.trigger_at_monotonic_ms + 2000
    with pytest.raises(ValidationError):
        DMAnnouncementSlot(
            domain_seq=1,
            room_id=ROOM_ID,
            revision=3,
            trigger_at_monotonic_ms=1000,
            admission_deadline_monotonic_ms=3001,
        )


def test_admission_result_requires_message_only_when_admitted() -> None:
    message = DMTemplateMessage(
        message_id=uuid4(),
        room_id=ROOM_ID,
        revision=3,
        channel="public",
        audience_bindings=(),
        text="昨夜平安。",
        source="template",
    )
    admitted = DMAdmissionResult(
        domain_seq=1,
        transport_seq=1,
        admitted=True,
        message=message,
    )
    assert admitted.message == message

    with pytest.raises(ValidationError):
        DMAdmissionResult(
            domain_seq=1,
            transport_seq=0,
            admitted=False,
            message=None,
        ).model_copy(update={"message": message})
