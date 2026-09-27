import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_serializer, model_validator

from werewolf_dm.domain.enums import Phase
from werewolf_dm.domain.model import StrictModel

TemplateCategory = Literal[
    "PHASE_NOTICE",
    "DEATH_NOTICE",
    "EXILE_NOTICE",
    "NO_EXILE_NOTICE",
    "SEAT_PROMPT",
    "PAUSE_NOTICE",
    "TERMINAL_NOTICE",
]
TemplateChannel = Literal["public", "seat"]
TemplateStyle = Literal["neutral", "formal", "urgent"]
TemplateFactKind = Literal[
    "phase",
    "player_died",
    "player_exiled",
    "no_exile",
    "vote_summary",
    "seat_prompt",
    "game_ended",
]
TemplateVisibility = Literal["public", "seat"]
AdmissionStatus = Literal["admitted", "suppressed", "failed"]
SuppressReason = Literal[
    "stale_revision",
    "stale_phase",
    "room_closed",
    "duplicate_slot",
    "admission_timeout",
    "transport_failed",
]

ACTIVE_TEMPLATE_CATEGORIES = frozenset(
    {
        "PHASE_NOTICE",
        "DEATH_NOTICE",
        "EXILE_NOTICE",
        "NO_EXILE_NOTICE",
        "SEAT_PROMPT",
        "PAUSE_NOTICE",
        "TERMINAL_NOTICE",
    }
)
ACTIVE_TEMPLATE_FACT_KINDS = frozenset(
    {
        "phase",
        "player_died",
        "player_exiled",
        "no_exile",
        "vote_summary",
        "seat_prompt",
        "game_ended",
    }
)
ACTIVE_TEMPLATE_ROUTES = frozenset(
    {
        ("PHASE_NOTICE", "public", "neutral"),
        ("DEATH_NOTICE", "public", "formal"),
        ("EXILE_NOTICE", "public", "formal"),
        ("NO_EXILE_NOTICE", "public", "neutral"),
        ("SEAT_PROMPT", "seat", "urgent"),
        ("PAUSE_NOTICE", "public", "urgent"),
        ("TERMINAL_NOTICE", "public", "formal"),
    }
)


class DMTemplateRejectReason(StrEnum):
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    INVALID_CATALOG = "INVALID_CATALOG"
    FACT_NOT_ALLOWED = "FACT_NOT_ALLOWED"
    UNSAFE_TEMPLATE_TEXT = "UNSAFE_TEMPLATE_TEXT"
    CONFUSABLE_TEMPLATE_TEXT = "CONFUSABLE_TEMPLATE_TEXT"
    PLACEHOLDER_MISMATCH = "PLACEHOLDER_MISMATCH"
    NO_FACTS_USED = "NO_FACTS_USED"
    STALE_REVISION = "stale_revision"
    STALE_PHASE = "stale_phase"
    ROOM_CLOSED = "room_closed"
    DUPLICATE_SLOT = "duplicate_slot"
    ADMISSION_TIMEOUT = "admission_timeout"
    TRANSPORT_FAILED = "transport_failed"


class TemplateAudience(StrictModel):
    seat_id: int | None = Field(default=None, ge=1, le=6)
    session_id: UUID | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if (self.seat_id is None) != (self.session_id is None):
            raise ValueError("seat audience requires both seat_id and session_id")
        return self


class TemplateVariantKey(StrictModel):
    catalog_version: str = Field(min_length=1)
    category: TemplateCategory
    channel: TemplateChannel
    style: TemplateStyle
    audience_bindings: tuple[TemplateAudience, ...]
    source_event_id: UUID

    @model_validator(mode="after")
    def validate_audience_cardinality(self) -> Self:
        bindings = tuple(
            sorted(
                self.audience_bindings,
                key=lambda binding: (
                    binding.seat_id is None,
                    binding.seat_id or 0,
                    str(binding.session_id) if binding.session_id is not None else "",
                ),
            )
        )
        if len(set(bindings)) != len(bindings):
            raise ValueError("audience bindings must be unique")
        if self.channel == "public" and bindings:
            raise ValueError("public variant key requires zero audience bindings")
        if self.channel == "seat" and (
            len(bindings) != 1 or bindings[0].seat_id is None or bindings[0].session_id is None
        ):
            raise ValueError(
                "seat variant key requires one bound seat and session",
            )
        object.__setattr__(self, "audience_bindings", bindings)
        return self


class TemplateIntent(StrictModel):
    intent_id: UUID
    source_event_id: UUID
    source_revision: int = Field(ge=0)
    source_phase: Phase
    catalog_version: str = Field(min_length=1)
    category: TemplateCategory
    channel: TemplateChannel
    audience_seat_ids: tuple[int, ...]
    audience_bindings: tuple[TemplateAudience, ...]
    template_variant_id: str = Field(min_length=1)
    style: TemplateStyle
    source_event_ids: tuple[UUID, ...]
    allowed_fact_kinds: frozenset[str]
    source: Literal["template"] = "template"

    @model_validator(mode="after")
    def validate_audience_and_event_ids(self) -> Self:
        seat_ids = sorted(set(self.audience_seat_ids))
        if any(seat_id < 1 or seat_id > 6 for seat_id in seat_ids):
            raise ValueError("audience seat ids must be between 1 and 6")
        bindings = sorted(
            self.audience_bindings,
            key=lambda binding: (
                binding.seat_id is None,
                binding.seat_id or 0,
                str(binding.session_id) if binding.session_id is not None else "",
            ),
        )
        if len(set(bindings)) != len(bindings):
            raise ValueError("audience bindings must be unique")
        event_ids = sorted(set(self.source_event_ids))
        if self.source_event_id not in event_ids:
            raise ValueError("source_event_id must be present in source_event_ids")
        if not self.allowed_fact_kinds <= ACTIVE_TEMPLATE_FACT_KINDS:
            raise ValueError("unknown template fact kind")
        if self.channel == "public":
            if seat_ids or bindings:
                raise ValueError("public template intent cannot carry seat audience")
        elif len(bindings) != 1 or bindings[0].seat_id is None:
            raise ValueError("seat template intent requires exactly one seat binding")
        elif seat_ids != [bindings[0].seat_id]:
            raise ValueError("audience_seat_ids must match the seat binding")

        object.__setattr__(self, "audience_seat_ids", tuple(seat_ids))
        object.__setattr__(self, "audience_bindings", tuple(bindings))
        object.__setattr__(self, "source_event_ids", tuple(event_ids))
        return self


class TemplateFact(StrictModel):
    fact_id: UUID
    source_event_id: UUID
    visibility: TemplateVisibility
    audience_seat_ids: tuple[int, ...]
    kind: TemplateFactKind
    fields: Mapping[str, str | int]

    @model_validator(mode="after")
    def validate_audience_and_fields(self) -> Self:
        seat_ids = sorted(set(self.audience_seat_ids))
        if any(seat_id < 1 or seat_id > 6 for seat_id in seat_ids):
            raise ValueError("fact audience seat ids must be between 1 and 6")
        if self.visibility == "public" and seat_ids:
            raise ValueError("public fact cannot carry seat audience")
        if self.visibility == "seat" and len(seat_ids) != 1:
            raise ValueError("seat fact requires exactly one audience seat")
        allowed_fields = _FACT_FIELDS[self.kind]
        if set(self.fields) != allowed_fields:
            raise ValueError(f"{self.kind} facts require exactly {sorted(allowed_fields)}")
        object.__setattr__(self, "audience_seat_ids", tuple(seat_ids))
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        return self

    @field_serializer("fields")
    def serialize_fields(self, fields: Mapping[str, str | int]) -> dict[str, str | int]:
        return dict(fields)


class TemplateVariant(StrictModel):
    template_variant_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    channel: TemplateChannel
    style: str = Field(min_length=1)
    template_text: str = Field(min_length=1)
    allowed_fact_kinds: frozenset[str]
    placeholders: frozenset[str]
    deprecated_at_version: str | None = None

    @model_validator(mode="after")
    def validate_catalog_shape(self) -> Self:
        if self.category not in ACTIVE_TEMPLATE_CATEGORIES:
            raise ValueError("unknown template category")
        if self.style not in {"neutral", "formal", "urgent"}:
            raise ValueError("unknown template style")
        if not self.allowed_fact_kinds <= ACTIVE_TEMPLATE_FACT_KINDS:
            raise ValueError("unknown template fact kind")
        validate_template_text(self.template_text)
        if self.placeholders != _template_placeholders(self.template_text):
            raise ValueError("PLACEHOLDER_MISMATCH")
        allowed_placeholders = frozenset(
            placeholder
            for fact_kind in self.allowed_fact_kinds
            for placeholder in _FACT_FIELDS[fact_kind]
        )
        if not self.placeholders <= allowed_placeholders:
            raise ValueError("PLACEHOLDER_MISMATCH")
        return self


class TemplateCatalog(StrictModel):
    catalog_version: str = Field(min_length=1)
    variants: Mapping[str, TemplateVariant]
    reserved_variant_ids: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_and_freeze_variants(self) -> Self:
        variants = dict(self.variants)
        if not variants:
            raise ValueError("TEMPLATE_CATALOG_EMPTY")
        variant_ids = [variant.template_variant_id for variant in variants.values()]
        if len(set(variant_ids)) != len(variant_ids):
            raise ValueError("template variant ids must be unique")
        if any(key != variant.template_variant_id for key, variant in variants.items()):
            raise ValueError("template catalog keys must equal variant ids")
        routes = [
            (variant.category, variant.channel, variant.style) for variant in variants.values()
        ]
        if len(set(routes)) != len(routes):
            raise ValueError("TEMPLATE_ROUTE_COLLISION")
        if set(routes) != ACTIVE_TEMPLATE_ROUTES:
            raise ValueError("TEMPLATE_ROUTE_COVERAGE")
        object.__setattr__(self, "variants", MappingProxyType(variants))
        return self

    @field_serializer("variants")
    def serialize_variants(
        self,
        variants: Mapping[str, TemplateVariant],
    ) -> dict[str, TemplateVariant]:
        return dict(variants)


class TemplateRenderRequest(StrictModel):
    intent: TemplateIntent
    catalog_version: str = Field(min_length=1)
    facts: tuple[TemplateFact, ...]

    @model_validator(mode="after")
    def freeze_facts(self) -> Self:
        object.__setattr__(self, "facts", tuple(self.facts))
        return self


class TemplateRenderResult(StrictModel):
    intent_id: UUID
    template_variant_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    final_text: str = Field(min_length=1)
    source_event_ids: tuple[UUID, ...]
    unused_fact_ids: tuple[UUID, ...]
    channel: TemplateChannel
    audience_bindings: tuple[TemplateAudience, ...]
    source: Literal["template"] = "template"

    @model_validator(mode="after")
    def validate_sorted_ids_and_audience(self) -> Self:
        source_event_ids = sorted(set(self.source_event_ids))
        unused_fact_ids = sorted(set(self.unused_fact_ids))
        bindings = sorted(
            self.audience_bindings,
            key=lambda binding: (
                binding.seat_id is None,
                binding.seat_id or 0,
                str(binding.session_id) if binding.session_id is not None else "",
            ),
        )
        if self.channel == "public" and bindings:
            raise ValueError("public result cannot carry seat audience")
        if self.channel == "seat" and (
            len(bindings) != 1 or bindings[0].seat_id is None or bindings[0].session_id is None
        ):
            raise ValueError(
                "seat result requires one bound seat and session",
            )
        object.__setattr__(self, "source_event_ids", tuple(source_event_ids))
        object.__setattr__(self, "unused_fact_ids", tuple(unused_fact_ids))
        object.__setattr__(self, "audience_bindings", tuple(bindings))
        return self


class DMTemplateMessage(StrictModel):
    message_id: UUID
    room_id: UUID
    revision: int = Field(ge=0)
    channel: TemplateChannel
    audience_bindings: tuple[TemplateAudience, ...]
    text: str = Field(min_length=1)
    source: Literal["template"]

    @model_validator(mode="after")
    def validate_audience(self) -> Self:
        bindings = sorted(
            self.audience_bindings,
            key=lambda binding: (
                binding.seat_id is None,
                binding.seat_id or 0,
                str(binding.session_id) if binding.session_id is not None else "",
            ),
        )
        if self.channel == "public" and bindings:
            raise ValueError("public message cannot carry seat audience")
        if self.channel == "seat" and (
            len(bindings) != 1 or bindings[0].seat_id is None or bindings[0].session_id is None
        ):
            raise ValueError(
                "seat message requires one bound seat and session",
            )
        object.__setattr__(self, "audience_bindings", tuple(bindings))
        return self


class DMTraceRecord(StrictModel):
    trace_id: UUID
    intent_id: UUID
    template_variant_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    source_event_ids: tuple[UUID, ...]
    channel: TemplateChannel
    audience_seat_ids: tuple[int, ...]
    admission_status: AdmissionStatus
    suppress_reason: SuppressReason | None = None
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_reason_and_ids(self) -> Self:
        if self.admission_status == "suppressed" and self.suppress_reason is None:
            raise ValueError("suppressed trace requires suppress_reason")
        if self.admission_status != "suppressed" and self.suppress_reason is not None:
            raise ValueError("only suppressed trace may carry suppress_reason")
        seat_ids = sorted(set(self.audience_seat_ids))
        event_ids = sorted(set(self.source_event_ids))
        if self.channel == "public" and seat_ids:
            raise ValueError("public trace cannot carry seat audience")
        if self.channel == "seat" and len(seat_ids) != 1:
            raise ValueError("seat trace requires exactly one audience seat")
        object.__setattr__(self, "audience_seat_ids", tuple(seat_ids))
        object.__setattr__(self, "source_event_ids", tuple(event_ids))
        return self


class DMAnnouncementSlot(StrictModel):
    domain_seq: int = Field(ge=1)
    room_id: UUID
    revision: int = Field(ge=0)
    trigger_at_monotonic_ms: int = Field(ge=0)
    admission_deadline_monotonic_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_deadline(self) -> Self:
        if self.admission_deadline_monotonic_ms != self.trigger_at_monotonic_ms + 2000:
            raise ValueError("admission deadline must be trigger time plus 2000ms")
        return self


class DMAdmissionResult(StrictModel):
    domain_seq: int = Field(ge=1)
    transport_seq: int = Field(ge=0)
    admitted: bool
    message: DMTemplateMessage | None

    @model_validator(mode="after")
    def validate_message(self) -> Self:
        if self.admitted and self.message is None:
            raise ValueError("admitted result requires a template message")
        if not self.admitted and self.message is not None:
            raise ValueError("suppressed result cannot carry a template message")
        return self


_FACT_FIELDS: dict[str, frozenset[str]] = {
    "phase": frozenset({"phase_label", "day"}),
    "player_died": frozenset({"seat_ids", "count"}),
    "player_exiled": frozenset({"seat_id"}),
    "no_exile": frozenset({"reason_label"}),
    "vote_summary": frozenset({"submitted_count", "eligible_count"}),
    "seat_prompt": frozenset({"seat_id", "phase_label"}),
    "game_ended": frozenset({"winner_label"}),
}
_UNSAFE_CONTROL_PATTERN = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_ALLOWED_PUNCTUATION = frozenset(
    " \t\n"
    "{}:,.!?;()[]-_%/"
    "\uff0c\u3002\uff01\uff1f\uff1a\uff1b\u3001\uff08\uff09"
    "\u201c\u201d\u2018\u2019\u300a\u300b\u3010\u3011\u2026\u2014\u00b7"
)


def _template_placeholders(template_text: str) -> frozenset[str]:
    placeholders = frozenset(re.findall(r"\{([a-z][a-z0-9_]*)\}", template_text))
    without_matches = re.sub(r"\{[a-z][a-z0-9_]*\}", "", template_text)
    if "{" in without_matches or "}" in without_matches:
        raise ValueError("PLACEHOLDER_MISMATCH")
    return placeholders


def validate_template_text(template_text: str) -> None:
    if _UNSAFE_CONTROL_PATTERN.search(template_text):
        raise ValueError("UNSAFE_TEMPLATE_TEXT")
    if any(
        unicodedata.category(character).startswith("C")
        for character in template_text
        if character not in "\n\t"
    ):
        raise ValueError("UNSAFE_TEMPLATE_TEXT")
    if any(not _is_safe_template_character(character) for character in template_text):
        raise ValueError("CONFUSABLE_TEMPLATE_TEXT")


def _is_safe_template_character(character: str) -> bool:
    if character in _ALLOWED_PUNCTUATION or character.isascii():
        return True
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )
