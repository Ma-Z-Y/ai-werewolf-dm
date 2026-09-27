from uuid import UUID

import pytest

from werewolf_dm.application.dm_contracts import (
    TemplateAudience,
    TemplateCatalog,
    TemplateFact,
    TemplateIntent,
    TemplateRenderRequest,
    TemplateVariant,
)
from werewolf_dm.application.dm_templates import TemplateRegistry, render_template
from werewolf_dm.domain.enums import Phase

CATALOG_VERSION = "s4-template-v1"
INTENT_ID = UUID("00000000-0000-0000-0000-000000000301")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000101")
OTHER_EVENT_ID = UUID("00000000-0000-0000-0000-000000000102")
SESSION_ID = UUID("00000000-0000-0000-0000-000000000201")
DEATH_FACT_ID = UUID("00000000-0000-0000-0000-000000000401")
EXTRA_FACT_ID = UUID("00000000-0000-0000-0000-000000000402")
LAST_EXTRA_FACT_ID = UUID("00000000-0000-0000-0000-000000000403")
THIRD_EXTRA_FACT_ID = UUID("00000000-0000-0000-0000-000000000404")


def _death_intent(
    *,
    source_event_id: UUID = EVENT_ID,
    source_event_ids: tuple[UUID, ...] | None = None,
) -> TemplateIntent:
    return TemplateIntent(
        intent_id=INTENT_ID,
        source_event_id=source_event_id,
        source_revision=3,
        source_phase=Phase.DAY_ANNOUNCE,
        catalog_version=CATALOG_VERSION,
        category="DEATH_NOTICE",
        channel="public",
        audience_seat_ids=(),
        audience_bindings=(),
        template_variant_id="s4-public-death-formal-v1",
        style="formal",
        source_event_ids=source_event_ids or (source_event_id,),
        allowed_fact_kinds=frozenset({"player_died"}),
    )


def _seat_prompt_intent() -> TemplateIntent:
    audience = TemplateAudience(seat_id=2, session_id=SESSION_ID)
    return TemplateIntent(
        intent_id=UUID("00000000-0000-0000-0000-000000000302"),
        source_event_id=EVENT_ID,
        source_revision=4,
        source_phase=Phase.NIGHT_WITCH,
        catalog_version=CATALOG_VERSION,
        category="SEAT_PROMPT",
        channel="seat",
        audience_seat_ids=(2,),
        audience_bindings=(audience,),
        template_variant_id="s4-seat-prompt-urgent-v1",
        style="urgent",
        source_event_ids=(EVENT_ID,),
        allowed_fact_kinds=frozenset({"seat_prompt"}),
    )


def _phase_intent(source_phase: Phase) -> TemplateIntent:
    return TemplateIntent(
        intent_id=UUID("00000000-0000-0000-0000-000000000303"),
        source_event_id=EVENT_ID,
        source_revision=3,
        source_phase=source_phase,
        catalog_version=CATALOG_VERSION,
        category="PHASE_NOTICE",
        channel="public",
        audience_seat_ids=(),
        audience_bindings=(),
        template_variant_id="s4-public-phase-neutral-v1",
        style="neutral",
        source_event_ids=(EVENT_ID,),
        allowed_fact_kinds=frozenset({"phase"}),
    )


def _fact(
    *,
    fact_id: UUID = DEATH_FACT_ID,
    source_event_id: UUID = EVENT_ID,
    visibility: str = "public",
    audience_seat_ids: tuple[int, ...] = (),
    kind: str = "player_died",
    fields: dict[str, str | int] | None = None,
) -> TemplateFact:
    return TemplateFact(
        fact_id=fact_id,
        source_event_id=source_event_id,
        visibility=visibility,
        audience_seat_ids=audience_seat_ids,
        kind=kind,
        fields=fields or {"seat_ids": "2,4", "count": 2},
    )


def _request(
    facts: tuple[TemplateFact, ...],
    *,
    intent: TemplateIntent | None = None,
) -> TemplateRenderRequest:
    return TemplateRenderRequest(
        intent=intent or _death_intent(),
        catalog_version=CATALOG_VERSION,
        facts=facts,
    )


def _default_catalog() -> TemplateCatalog:
    return TemplateRegistry().catalog(CATALOG_VERSION)


def _catalog_with_variant(
    variant_id: str,
    **updates: object,
) -> TemplateCatalog:
    catalog = _default_catalog()
    variant = TemplateVariant.model_construct(
        **{**catalog.variants[variant_id].model_dump(), **updates}
    )
    return TemplateCatalog.model_construct(
        catalog_version=catalog.catalog_version,
        variants={**catalog.variants, variant_id: variant},
        reserved_variant_ids=catalog.reserved_variant_ids,
    )


def _catalog_without_placeholders() -> TemplateCatalog:
    catalog = _default_catalog()
    variant_id = "s4-public-death-formal-v1"
    replacement = TemplateVariant(
        template_variant_id=variant_id,
        category="DEATH_NOTICE",
        channel="public",
        style="formal",
        template_text="昨夜有人出局。",
        allowed_fact_kinds=frozenset({"player_died"}),
        placeholders=frozenset(),
    )
    return catalog.model_copy(update={"variants": {**catalog.variants, variant_id: replacement}})


def test_renderer_produces_exact_server_owned_text() -> None:
    result = render_template(_request((_fact(),)), _default_catalog())

    assert result.final_text == "昨夜,2,4 号玩家出局。"
    assert result.template_variant_id == _death_intent().template_variant_id
    assert result.source_event_ids == (EVENT_ID,)
    assert result.source == "template"
    dumped = result.model_dump()
    assert "prefix_text" not in dumped
    assert "suffix_text" not in dumped
    assert "prompt" not in dumped
    assert "provider" not in dumped
    assert "model" not in dumped
    assert "raw_output" not in dumped


def test_renderer_rejects_unauthorized_fact() -> None:
    private_fact = _fact(
        visibility="seat",
        audience_seat_ids=(2,),
    )

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(_request((private_fact,)), _default_catalog())


def test_renderer_omits_player_speech_and_claimed_text() -> None:
    claim_fact = _fact(
        fields={
            "seat_ids": "[玩家3声称] 我是预言家",
            "count": 1,
        }
    )

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(_request((claim_fact,)), _default_catalog())

    result = render_template(_request((_fact(),)), _default_catalog())
    assert "玩家3声称" not in result.final_text
    assert list(result.source_event_ids) == sorted(result.source_event_ids)


def test_renderer_rejects_unmarked_player_speech_in_fact_fields() -> None:
    claim_fact = _fact(fields={"seat_ids": "我是预言家", "count": 1})

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(_request((claim_fact,)), _default_catalog())


@pytest.mark.parametrize(
    "seat_ids",
    ["WEREWOLF", "狼人", "预言家", "女巫药水", "死于WOLF"],
)
def test_renderer_rejects_hidden_identity_values(seat_ids: str) -> None:
    hidden_fact = _fact(fields={"seat_ids": seat_ids, "count": 1})

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(_request((hidden_fact,)), _default_catalog())


@pytest.mark.parametrize(
    "template_text",
    ["SPEECH: {seat_ids} 出局。", "玩家声称: {seat_ids} 出局。"],
)
def test_renderer_rejects_claim_and_speech_in_template_text(template_text: str) -> None:
    catalog = _catalog_with_variant(
        "s4-public-death-formal-v1",
        template_text=template_text,
    )

    with pytest.raises(ValueError, match="UNSAFE_TEMPLATE_TEXT"):
        render_template(_request((_fact(),)), catalog)


@pytest.mark.parametrize(
    "fields",
    [
        {"seat_ids": "7", "count": 1},
        {"seat_ids": "2,4", "count": 1},
        {"seat_ids": "4,2", "count": 2},
    ],
)
def test_renderer_rejects_noncanonical_fact_fields(fields: dict[str, str | int]) -> None:
    invalid_fact = _fact(fields=fields)

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(_request((invalid_fact,)), _default_catalog())


def test_renderer_rejects_noncanonical_seat_id_leading_zero() -> None:
    invalid_fact = _fact(fields={"seat_ids": "02", "count": 1})

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(_request((invalid_fact,)), _default_catalog())


def test_renderer_binds_phase_label_to_intent_source_phase() -> None:
    wrong_phase_fact = _fact(
        kind="phase",
        fields={"phase_label": "玩家准备", "day": 1},
    )
    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(
            _request((wrong_phase_fact,), intent=_phase_intent(Phase.DAY_VOTE)),
            _default_catalog(),
        )

    wrong_seat_phase_fact = _fact(
        visibility="seat",
        audience_seat_ids=(2,),
        kind="seat_prompt",
        fields={"seat_id": 2, "phase_label": "狼人行动"},
    )
    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(
            _request((wrong_seat_phase_fact,), intent=_seat_prompt_intent()),
            _default_catalog(),
        )

    matching_fact = _fact(
        kind="phase",
        fields={"phase_label": "白天投票", "day": 1},
    )
    result = render_template(
        _request((matching_fact,), intent=_phase_intent(Phase.DAY_VOTE)),
        _default_catalog(),
    )
    assert result.final_text == "当前阶段:白天投票,第 1 天。"


@pytest.mark.parametrize("unsafe_text", ["当前\u200b阶段", "当前\u202e阶段"])
def test_renderer_rejects_zero_width_and_bidi_controls(unsafe_text: str) -> None:
    catalog = _catalog_with_variant(
        "s4-public-death-formal-v1",
        template_text=f"昨夜，{unsafe_text}出局。",  # noqa: RUF001
    )

    with pytest.raises(ValueError, match="UNSAFE_TEMPLATE_TEXT"):
        render_template(_request((_fact(),)), catalog)


def test_renderer_rejects_confusable_template_and_fact_text() -> None:
    confusable_catalog = _catalog_with_variant(
        "s4-public-death-formal-v1",
        template_text="昨夜，ЅEER 出局。",  # noqa: RUF001
    )
    with pytest.raises(ValueError, match="CONFUSABLE_TEMPLATE_TEXT"):
        render_template(_request((_fact(),)), confusable_catalog)

    confusable_fact = _fact(fields={"seat_ids": "ЅEER", "count": 1})  # noqa: RUF001
    with pytest.raises(ValueError, match="CONFUSABLE_TEMPLATE_TEXT"):
        render_template(_request((confusable_fact,)), _default_catalog())


def test_renderer_normalizes_nfkc_fact_text() -> None:
    result = render_template(
        _request((_fact(fields={"seat_ids": "２，４", "count": 2}),)),  # noqa: RUF001
        _default_catalog(),
    )

    assert result.final_text == "昨夜,2,4 号玩家出局。"


def test_renderer_records_unused_facts() -> None:
    result = render_template(
        _request(
            (
                _fact(fact_id=LAST_EXTRA_FACT_ID),
                _fact(fact_id=EXTRA_FACT_ID),
                _fact(),
                _fact(fact_id=THIRD_EXTRA_FACT_ID),
            )
        ),
        _default_catalog(),
    )

    assert result.unused_fact_ids == (
        EXTRA_FACT_ID,
        LAST_EXTRA_FACT_ID,
        THIRD_EXTRA_FACT_ID,
    )


@pytest.mark.parametrize(
    ("first_seat_ids", "second_seat_ids"),
    [("2", "4"), ("4", "2")],
)
def test_renderer_rejects_duplicate_fact_ids(
    first_seat_ids: str,
    second_seat_ids: str,
) -> None:
    first = _fact(fields={"seat_ids": first_seat_ids, "count": 1})
    second = _fact(fields={"seat_ids": second_seat_ids, "count": 1})

    with pytest.raises(ValueError, match="DUPLICATE_FACT_ID"):
        render_template(_request((first, second)), _default_catalog())


def test_renderer_fails_closed_when_no_fact_is_used() -> None:
    with pytest.raises(ValueError, match="NO_FACTS_USED"):
        render_template(_request((_fact(),)), _catalog_without_placeholders())


def test_renderer_rejects_catalog_version_mismatch() -> None:
    request = _request((_fact(),)).model_copy(update={"catalog_version": "other-catalog"})

    with pytest.raises(ValueError, match="INVALID_CATALOG"):
        render_template(request, _default_catalog())


def test_renderer_rejects_intent_variant_mismatch() -> None:
    intent = _death_intent().model_copy(update={"template_variant_id": "other-variant"})

    with pytest.raises(ValueError, match="TEMPLATE_MISMATCH"):
        render_template(_request((_fact(),), intent=intent), _default_catalog())


def test_renderer_rejects_fact_source_event_outside_intent() -> None:
    unrelated_fact = _fact(source_event_id=OTHER_EVENT_ID)

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(_request((unrelated_fact,)), _default_catalog())


def test_renderer_excludes_unused_source_event_ids() -> None:
    intent = _death_intent(source_event_ids=(EVENT_ID, OTHER_EVENT_ID))
    result = render_template(_request((_fact(),), intent=intent), _default_catalog())

    assert result.source_event_ids == (EVENT_ID,)


def test_renderer_allows_own_seat_fact_and_rejects_another_seat() -> None:
    own_fact = _fact(
        visibility="seat",
        audience_seat_ids=(2,),
        kind="seat_prompt",
        fields={"seat_id": 2, "phase_label": "女巫行动"},
    )
    result = render_template(
        _request((own_fact,), intent=_seat_prompt_intent()),
        _default_catalog(),
    )

    assert result.final_text == "请 2 号玩家在 女巫行动 行动。"

    other_fact = _fact(
        visibility="seat",
        audience_seat_ids=(3,),
        kind="seat_prompt",
        fields={"seat_id": 3, "phase_label": "女巫行动"},
    )

    with pytest.raises(ValueError, match="FACT_NOT_ALLOWED"):
        render_template(
            _request((other_fact,), intent=_seat_prompt_intent()),
            _default_catalog(),
        )
