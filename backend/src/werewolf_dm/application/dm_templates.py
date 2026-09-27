import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType

from werewolf_dm.application.dm_contracts import (
    TemplateCatalog,
    TemplateIntent,
    TemplateVariant,
    TemplateVariantKey,
)

_CATALOG_VERSION = "s4-template-v1"


def template_variant_descriptor(intent: TemplateIntent) -> TemplateVariantKey:
    bindings = tuple(
        sorted(
            intent.audience_bindings,
            key=lambda binding: (
                binding.seat_id is None,
                binding.seat_id or 0,
                str(binding.session_id) if binding.session_id is not None else "",
            ),
        )
    )
    return TemplateVariantKey(
        catalog_version=intent.catalog_version,
        category=intent.category,
        channel=intent.channel,
        style=intent.style,
        audience_bindings=bindings,
        source_event_id=intent.source_event_id,
    )


def template_variant_digest(key: TemplateVariantKey) -> str:
    bindings = sorted(
        (
            {
                "seat_id": binding.seat_id,
                "session_id": (str(binding.session_id) if binding.session_id is not None else None),
            }
            for binding in key.audience_bindings
        ),
        key=lambda binding: (
            binding["seat_id"] is None,
            binding["seat_id"] or 0,
            binding["session_id"] or "",
        ),
    )
    canonical = json.dumps(
        {
            "catalog_version": key.catalog_version,
            "category": key.category,
            "channel": key.channel,
            "style": key.style,
            "audience_bindings": bindings,
            "source_event_id": str(key.source_event_id),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class TemplateRegistry:
    def __init__(self, catalogs: Mapping[str, TemplateCatalog] | None = None) -> None:
        if catalogs is None:
            catalogs = _default_catalogs()
        if not catalogs:
            raise ValueError("CATALOG_NOT_FOUND")
        frozen_catalogs = dict(catalogs)
        if any(key != catalog.catalog_version for key, catalog in frozen_catalogs.items()):
            raise ValueError("CATALOG_KEY_MISMATCH")
        self._catalogs = MappingProxyType(frozen_catalogs)

    def catalog(self, catalog_version: str) -> TemplateCatalog:
        try:
            return self._catalogs[catalog_version]
        except KeyError as error:
            raise ValueError("CATALOG_NOT_FOUND") from error

    def select(self, key: TemplateVariantKey) -> TemplateVariant:
        catalog = self.catalog(key.catalog_version)
        matches = tuple(
            variant
            for variant in catalog.variants.values()
            if (
                variant.category == key.category
                and variant.channel == key.channel
                and variant.style == key.style
            )
        )
        if not matches:
            raise ValueError("TEMPLATE_VARIANT_NOT_FOUND")
        if len(matches) != 1:
            raise ValueError("TEMPLATE_VARIANT_AMBIGUOUS")
        return matches[0]

    def resolve(self, intent: TemplateIntent) -> TemplateVariant:
        return self.select(template_variant_descriptor(intent))


def _default_catalogs() -> dict[str, TemplateCatalog]:
    catalog = TemplateCatalog(
        catalog_version=_CATALOG_VERSION,
        variants={
            variant.template_variant_id: variant
            for variant in (
                TemplateVariant(
                    template_variant_id="s4-public-phase-neutral-v1",
                    category="PHASE_NOTICE",
                    channel="public",
                    style="neutral",
                    template_text="当前阶段：{phase_label}，第 {day} 天。",  # noqa: RUF001
                    allowed_fact_kinds=frozenset({"phase"}),
                    placeholders=frozenset({"phase_label", "day"}),
                ),
                TemplateVariant(
                    template_variant_id="s4-public-death-formal-v1",
                    category="DEATH_NOTICE",
                    channel="public",
                    style="formal",
                    template_text="昨夜，{seat_ids} 号玩家出局。",  # noqa: RUF001
                    allowed_fact_kinds=frozenset({"player_died"}),
                    placeholders=frozenset({"seat_ids"}),
                ),
                TemplateVariant(
                    template_variant_id="s4-public-exile-formal-v1",
                    category="EXILE_NOTICE",
                    channel="public",
                    style="formal",
                    template_text="投票结束，{seat_id} 号玩家被放逐。",  # noqa: RUF001
                    allowed_fact_kinds=frozenset({"player_exiled"}),
                    placeholders=frozenset({"seat_id"}),
                ),
                TemplateVariant(
                    template_variant_id="s4-public-no-exile-neutral-v1",
                    category="NO_EXILE_NOTICE",
                    channel="public",
                    style="neutral",
                    template_text="本轮无人出局，{reason_label}。",  # noqa: RUF001
                    allowed_fact_kinds=frozenset({"no_exile"}),
                    placeholders=frozenset({"reason_label"}),
                ),
                TemplateVariant(
                    template_variant_id="s4-seat-prompt-urgent-v1",
                    category="SEAT_PROMPT",
                    channel="seat",
                    style="urgent",
                    template_text="请 {seat_id} 号玩家在 {phase_label} 行动。",
                    allowed_fact_kinds=frozenset({"seat_prompt"}),
                    placeholders=frozenset({"seat_id", "phase_label"}),
                ),
                TemplateVariant(
                    template_variant_id="s4-public-pause-urgent-v1",
                    category="PAUSE_NOTICE",
                    channel="public",
                    style="urgent",
                    template_text="游戏已{phase_label}。",
                    allowed_fact_kinds=frozenset({"phase"}),
                    placeholders=frozenset({"phase_label"}),
                ),
                TemplateVariant(
                    template_variant_id="s4-public-terminal-formal-v1",
                    category="TERMINAL_NOTICE",
                    channel="public",
                    style="formal",
                    template_text="游戏结束，{winner_label}获胜。",  # noqa: RUF001
                    allowed_fact_kinds=frozenset({"game_ended"}),
                    placeholders=frozenset({"winner_label"}),
                ),
            )
        },
    )
    return {catalog.catalog_version: catalog}
