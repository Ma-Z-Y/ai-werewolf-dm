from uuid import UUID

import pytest
from pydantic import ValidationError

from werewolf_dm.application.dm_contracts import (
    TemplateAudience,
    TemplateCatalog,
    TemplateIntent,
    TemplateVariant,
    TemplateVariantKey,
)
from werewolf_dm.application.dm_templates import (
    TemplateRegistry,
    template_variant_descriptor,
    template_variant_digest,
)
from werewolf_dm.domain.enums import Phase

CATALOG_VERSION = "s4-template-v1"
EVENT_ID = UUID("00000000-0000-0000-0000-000000000101")
OTHER_EVENT_ID = UUID("00000000-0000-0000-0000-000000000102")
SESSION_ID = UUID("00000000-0000-0000-0000-000000000201")
OTHER_SESSION_ID = UUID("00000000-0000-0000-0000-000000000202")
EXPECTED_DEATH_DIGEST = "8a43d31dbd0b2906f89aaa8c52375d05ec956d79654e85aaf4552c61334dd6b5"


def _intent(
    *,
    source_event_id: UUID = EVENT_ID,
    category: str = "DEATH_NOTICE",
    channel: str = "public",
    style: str = "formal",
    audience_bindings: tuple[TemplateAudience, ...] = (),
    template_variant_id: str = "s4-public-death-formal-v1",
    allowed_fact_kinds: frozenset[str] = frozenset({"player_died"}),
) -> TemplateIntent:
    return TemplateIntent(
        intent_id=UUID("00000000-0000-0000-0000-000000000301"),
        source_event_id=source_event_id,
        source_revision=3,
        source_phase=Phase.DAY_ANNOUNCE,
        catalog_version=CATALOG_VERSION,
        category=category,
        channel=channel,
        audience_seat_ids=tuple(
            binding.seat_id for binding in audience_bindings if binding.seat_id is not None
        ),
        audience_bindings=audience_bindings,
        template_variant_id=template_variant_id,
        style=style,
        source_event_ids=(source_event_id,),
        allowed_fact_kinds=allowed_fact_kinds,
    )


def _key(
    *,
    catalog_version: str = CATALOG_VERSION,
    category: str = "DEATH_NOTICE",
    channel: str = "public",
    style: str = "formal",
    audience_bindings: tuple[TemplateAudience, ...] = (),
    source_event_id: UUID = EVENT_ID,
) -> TemplateVariantKey:
    return TemplateVariantKey(
        catalog_version=catalog_version,
        category=category,
        channel=channel,
        style=style,
        audience_bindings=audience_bindings,
        source_event_id=source_event_id,
    )


def _default_variants() -> dict[str, TemplateVariant]:
    return dict(TemplateRegistry().catalog(CATALOG_VERSION).variants)


def test_template_variant_is_deterministic() -> None:
    intent = _intent()
    key = template_variant_descriptor(intent)
    first = template_variant_digest(key)
    second = template_variant_digest(key)
    variant = TemplateRegistry().select(key)

    assert first == second == EXPECTED_DEATH_DIGEST
    assert len(first) == 64
    assert first == first.lower()
    assert variant.template_variant_id == intent.template_variant_id
    assert TemplateRegistry().resolve(intent) == variant


def test_template_variant_descriptor_excludes_template_variant_id() -> None:
    intent = _intent()
    changed_output = intent.model_copy(
        update={"template_variant_id": "different-output-id"},
    )

    assert template_variant_descriptor(intent) == template_variant_descriptor(changed_output)


def test_template_variant_key_binds_cardinality_to_channel() -> None:
    assert _key().audience_bindings == ()
    assert _key(
        category="SEAT_PROMPT",
        channel="seat",
        style="urgent",
        audience_bindings=(TemplateAudience(seat_id=2, session_id=SESSION_ID),),
    ).audience_bindings

    with pytest.raises(ValidationError):
        _key(audience_bindings=(TemplateAudience(seat_id=2, session_id=SESSION_ID),))
    with pytest.raises(ValidationError):
        _key(category="SEAT_PROMPT", channel="seat", style="urgent")
    with pytest.raises(ValidationError):
        _key(
            category="SEAT_PROMPT",
            channel="seat",
            style="urgent",
            audience_bindings=(
                TemplateAudience(seat_id=2, session_id=SESSION_ID),
                TemplateAudience(seat_id=3, session_id=OTHER_SESSION_ID),
            ),
        )


def test_same_route_different_event_selects_same_variant() -> None:
    registry = TemplateRegistry()
    first = _key()
    second = _key(source_event_id=OTHER_EVENT_ID)

    assert template_variant_digest(first) != template_variant_digest(second)
    assert registry.select(first) == registry.select(second)
    assert template_variant_digest(
        _key(catalog_version="s4-template-v2")
    ) != template_variant_digest(first)


def test_registry_select_is_stateless_across_instances() -> None:
    key = template_variant_descriptor(_intent())
    expected = _intent().template_variant_id

    assert TemplateRegistry().select(key).template_variant_id == expected
    assert TemplateRegistry().select(key).template_variant_id == expected
    assert template_variant_digest(key) == template_variant_digest(key)


def test_registry_rejects_digest_only_query() -> None:
    assert not hasattr(TemplateRegistry(), "select_by_key")


def test_catalog_rejects_confusable_role_alias() -> None:
    with pytest.raises(ValueError, match="CONFUSABLE_TEMPLATE_TEXT"):
        TemplateVariant(
            template_variant_id="bad",
            category="PHASE_NOTICE",
            channel="public",
            style="neutral",
            template_text="请确认 ЅEER 行动。",  # noqa: RUF001
            allowed_fact_kinds=frozenset({"phase"}),
            placeholders=frozenset(),
        )


@pytest.mark.parametrize("unsafe_text", ["当前\u200b阶段", "当前\u202e阶段"])
def test_catalog_rejects_zero_width_and_bidi_controls(unsafe_text: str) -> None:
    with pytest.raises(ValueError, match="UNSAFE_TEMPLATE_TEXT"):
        TemplateVariant(
            template_variant_id="bad",
            category="PHASE_NOTICE",
            channel="public",
            style="neutral",
            template_text=unsafe_text,
            allowed_fact_kinds=frozenset({"phase"}),
            placeholders=frozenset(),
        )


def test_catalog_rejects_undeclared_and_missing_placeholders() -> None:
    with pytest.raises(ValueError, match="PLACEHOLDER_MISMATCH"):
        TemplateVariant(
            template_variant_id="bad",
            category="PHASE_NOTICE",
            channel="public",
            style="neutral",
            template_text="当前阶段：{phase_label}。",  # noqa: RUF001
            allowed_fact_kinds=frozenset({"phase"}),
            placeholders=frozenset(),
        )
    with pytest.raises(ValueError, match="PLACEHOLDER_MISMATCH"):
        TemplateVariant(
            template_variant_id="bad",
            category="PHASE_NOTICE",
            channel="public",
            style="neutral",
            template_text="当前阶段。",
            allowed_fact_kinds=frozenset({"phase"}),
            placeholders=frozenset({"phase_label"}),
        )


def test_catalog_rejects_speak_claim_marker() -> None:
    with pytest.raises(ValueError, match="UNSAFE_TEMPLATE_TEXT"):
        TemplateVariant(
            template_variant_id="bad",
            category="DEATH_NOTICE",
            channel="public",
            style="formal",
            template_text="SPEAK {seat_ids} 出局。",
            allowed_fact_kinds=frozenset({"player_died"}),
            placeholders=frozenset({"seat_ids"}),
        )


def test_catalog_rejects_variant_id_mismatch_and_unknown_fact_kind() -> None:
    variants = _default_variants()
    with pytest.raises(ValidationError):
        TemplateCatalog(
            catalog_version=CATALOG_VERSION,
            variants={"key": variants["s4-public-phase-neutral-v1"]},
        )
    with pytest.raises(ValidationError):
        TemplateVariant(
            template_variant_id="bad",
            category="PHASE_NOTICE",
            channel="public",
            style="neutral",
            template_text="当前阶段：{phase_label}。",  # noqa: RUF001
            allowed_fact_kinds=frozenset({"raw_speech"}),
            placeholders=frozenset({"phase_label"}),
        )


def test_catalog_binds_placeholders_to_declared_fact_kinds() -> None:
    with pytest.raises(ValidationError, match="PLACEHOLDER_MISMATCH"):
        TemplateVariant(
            template_variant_id="bad",
            category="PHASE_NOTICE",
            channel="public",
            style="neutral",
            template_text="游戏结束，{winner_label}获胜。",  # noqa: RUF001
            allowed_fact_kinds=frozenset({"phase"}),
            placeholders=frozenset({"winner_label"}),
        )


def test_catalog_rejects_duplicate_route_variant() -> None:
    variants = _default_variants()
    duplicate = variants["s4-public-death-formal-v1"].model_copy(
        update={"template_variant_id": "duplicate-death-route"}
    )
    variants["duplicate-death-route"] = duplicate

    with pytest.raises(ValueError, match="TEMPLATE_ROUTE_COLLISION"):
        TemplateCatalog(
            catalog_version=CATALOG_VERSION,
            variants=variants,
        )


def test_default_catalog_covers_all_active_routes_and_styles() -> None:
    catalog = TemplateRegistry().catalog(CATALOG_VERSION)
    routes = {
        (variant.category, variant.channel, variant.style) for variant in catalog.variants.values()
    }

    assert routes == {
        ("PHASE_NOTICE", "public", "neutral"),
        ("DEATH_NOTICE", "public", "formal"),
        ("EXILE_NOTICE", "public", "formal"),
        ("NO_EXILE_NOTICE", "public", "neutral"),
        ("SEAT_PROMPT", "seat", "urgent"),
        ("PAUSE_NOTICE", "public", "urgent"),
        ("TERMINAL_NOTICE", "public", "formal"),
    }


def test_registry_rejects_empty_alias_and_unknown_key() -> None:
    registry = TemplateRegistry()

    with pytest.raises(ValueError, match="CATALOG_NOT_FOUND"):
        registry.catalog("missing")
    with pytest.raises(ValueError, match="CATALOG_NOT_FOUND"):
        TemplateRegistry({})

    catalog = registry.catalog(CATALOG_VERSION)
    with pytest.raises(ValueError, match="CATALOG_KEY_MISMATCH"):
        TemplateRegistry({"alias": catalog})
    with pytest.raises(ValidationError, match="TEMPLATE_CATALOG_EMPTY"):
        TemplateCatalog(catalog_version="empty-v1", variants={})
    with pytest.raises(ValueError, match="CATALOG_NOT_FOUND"):
        registry.select(_key(catalog_version="missing-catalog"))
