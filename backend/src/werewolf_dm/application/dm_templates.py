import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType
from typing import NoReturn
from uuid import UUID

from werewolf_dm.application.dm_contracts import (
    DMTemplateRejectReason,
    TemplateCatalog,
    TemplateFact,
    TemplateIntent,
    TemplateRenderRequest,
    TemplateRenderResult,
    TemplateVariant,
    TemplateVariantKey,
    validate_template_text,
)
from werewolf_dm.domain.enums import Phase

_CATALOG_VERSION = "s4-template-v1"
_CLAIM_MARKER_PATTERN = re.compile("声称|SPEAK|SPEECH", re.IGNORECASE)
_HIDDEN_IDENTITY_PATTERN = re.compile("WEREWOLF|狼人|预言家|女巫|药水|查验", re.IGNORECASE)
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
_SAFE_PAUSE_LABELS = frozenset({"暂停", "恢复"})
_SAFE_NO_EXILE_LABELS = frozenset({"无人投票", "PK 平票", "PK 无有效票"})
_SAFE_WINNER_LABELS = frozenset({"好人阵营", "狼人阵营"})
_SAFE_SEAT_PROMPT_PHASE_LABELS = frozenset({"狼人行动", "预言家查验", "女巫行动"})


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


def render_template(
    request: TemplateRenderRequest,
    catalog: TemplateCatalog,
) -> TemplateRenderResult:
    intent = request.intent
    if (
        request.catalog_version != intent.catalog_version
        or catalog.catalog_version != request.catalog_version
    ):
        _reject(DMTemplateRejectReason.INVALID_CATALOG)
    _reject_duplicate_fact_ids(request.facts)

    variant = TemplateRegistry({catalog.catalog_version: catalog}).select(
        template_variant_descriptor(intent)
    )
    if variant.template_variant_id != intent.template_variant_id:
        _reject("TEMPLATE_MISMATCH")

    template_text = _normalize_template_text(variant.template_text)
    validated_facts = tuple(
        (fact, _validated_fact_fields(fact, intent, variant))
        for fact in sorted(request.facts, key=lambda item: item.fact_id)
    )

    bindings: dict[str, str | int] = {}
    used_fact_ids: set[UUID] = set()
    for placeholder in sorted(variant.placeholders):
        token = f"{{{placeholder}}}"
        if token not in template_text:
            _reject(DMTemplateRejectReason.PLACEHOLDER_MISMATCH)
        candidates = tuple(
            (fact, fields)
            for fact, fields in validated_facts
            if fact.kind in variant.allowed_fact_kinds and placeholder in fields
        )
        if not candidates:
            _reject(DMTemplateRejectReason.PLACEHOLDER_MISMATCH)
        fact, fields = candidates[0]
        bindings[placeholder] = fields[placeholder]
        used_fact_ids.add(fact.fact_id)

    if request.facts and not used_fact_ids:
        _reject(DMTemplateRejectReason.NO_FACTS_USED)

    final_text = template_text
    for placeholder, value in bindings.items():
        final_text = final_text.replace(f"{{{placeholder}}}", str(value))
    if not final_text or "{" in final_text or "}" in final_text:
        _reject(DMTemplateRejectReason.PLACEHOLDER_MISMATCH)

    source_event_ids = sorted(
        {intent.source_event_id}
        | {fact.source_event_id for fact in request.facts if fact.fact_id in used_fact_ids}
    )
    unused_fact_ids = sorted({fact.fact_id for fact in request.facts} - used_fact_ids)
    return TemplateRenderResult(
        intent_id=intent.intent_id,
        template_variant_id=variant.template_variant_id,
        catalog_version=request.catalog_version,
        final_text=final_text,
        source_event_ids=tuple(source_event_ids),
        unused_fact_ids=tuple(unused_fact_ids),
        channel=intent.channel,
        audience_bindings=intent.audience_bindings,
    )


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


def _normalize_template_text(template_text: str) -> str:
    normalized = unicodedata.normalize("NFKC", template_text)
    if _CLAIM_MARKER_PATTERN.search(normalized) or _HIDDEN_IDENTITY_PATTERN.search(normalized):
        _reject(DMTemplateRejectReason.UNSAFE_TEMPLATE_TEXT)
    validate_template_text(normalized)
    return normalized


def _normalize_fact_text(fact_text: str) -> str:
    normalized = unicodedata.normalize("NFKC", fact_text)
    if "{" in normalized or "}" in normalized:
        _reject(DMTemplateRejectReason.PLACEHOLDER_MISMATCH)
    if _CLAIM_MARKER_PATTERN.search(normalized):
        _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
    validate_template_text(normalized)
    return normalized


def _validated_fact_fields(
    fact: TemplateFact,
    intent: TemplateIntent,
    variant: TemplateVariant,
) -> dict[str, str | int]:
    if fact.kind not in intent.allowed_fact_kinds:
        _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
    if fact.kind not in variant.allowed_fact_kinds:
        _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
    if fact.source_event_id not in intent.source_event_ids:
        _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
    if intent.channel == "public":
        if fact.visibility != "public" or fact.audience_seat_ids:
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
    elif fact.visibility == "seat":
        if fact.audience_seat_ids != intent.audience_seat_ids:
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
    elif fact.audience_seat_ids:
        _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)

    fields = {
        key: _normalize_fact_text(value) if isinstance(value, str) else value
        for key, value in fact.fields.items()
    }
    _validate_fact_field_values(fact.kind, fields, intent)
    return fields


def _validate_fact_field_values(
    kind: str,
    fields: dict[str, str | int],
    intent: TemplateIntent,
) -> None:
    if kind == "phase":
        phase_label = fields.get("phase_label")
        if intent.category == "PHASE_NOTICE":
            valid_phase_label = phase_label == _PHASE_LABELS[intent.source_phase]
        elif intent.category == "PAUSE_NOTICE":
            valid_phase_label = phase_label in _SAFE_PAUSE_LABELS
        else:
            valid_phase_label = False
        if not valid_phase_label or not _is_int_between(fields.get("day"), 0, 999):
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
        return
    if kind == "player_died":
        seat_ids = _parse_seat_ids(fields.get("seat_ids"))
        count = _bounded_int(fields.get("count"), 1, 6)
        if not seat_ids or count is None or count != len(seat_ids):
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
        return
    if kind == "player_exiled":
        if not _is_int_between(fields.get("seat_id"), 1, 6):
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
        return
    if kind == "no_exile":
        if fields.get("reason_label") not in _SAFE_NO_EXILE_LABELS:
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
        return
    if kind == "vote_summary":
        submitted = _bounded_int(fields.get("submitted_count"), 0, 6)
        eligible = _bounded_int(fields.get("eligible_count"), 0, 6)
        if submitted is None or eligible is None or submitted > eligible:
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
        return
    if kind == "seat_prompt":
        seat_id = fields.get("seat_id")
        expected_phase_label = _PHASE_LABELS[intent.source_phase]
        if (
            not _is_int_between(seat_id, 1, 6)
            or seat_id not in intent.audience_seat_ids
            or expected_phase_label not in _SAFE_SEAT_PROMPT_PHASE_LABELS
            or fields.get("phase_label") != expected_phase_label
        ):
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
        return
    if kind == "game_ended":
        if fields.get("winner_label") not in _SAFE_WINNER_LABELS:
            _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)
        return
    _reject(DMTemplateRejectReason.FACT_NOT_ALLOWED)


def _parse_seat_ids(value: str | int | None) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not value:
        return None
    parts = value.split(",")
    if any(len(part) != 1 or part not in "123456" for part in parts):
        return None
    seat_ids = tuple(int(part) for part in parts)
    if seat_ids != tuple(sorted(set(seat_ids))):
        return None
    return seat_ids


def _is_int_between(value: str | int | None, minimum: int, maximum: int) -> bool:
    return _bounded_int(value, minimum, maximum) is not None


def _bounded_int(value: str | int | None, minimum: int, maximum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum:
        return value
    return None


def _reject_duplicate_fact_ids(facts: tuple[TemplateFact, ...]) -> None:
    fact_ids = [fact.fact_id for fact in facts]
    if len(set(fact_ids)) != len(fact_ids):
        _reject("DUPLICATE_FACT_ID")


def _reject(reason: DMTemplateRejectReason | str) -> NoReturn:
    code = reason.value if isinstance(reason, DMTemplateRejectReason) else reason
    raise ValueError(code)


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
