from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from itertools import pairwise
from types import MappingProxyType
from typing import Self, cast
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from werewolf_dm.application.dm_contracts import DMTraceRecord
from werewolf_dm.domain.model import StrictModel

# Pydantic's model_construct() is an internal validation-bypassing API and is
# not part of the metrics trust boundary. Application code must use normal
# construction, validation, copy, or serialization APIs.
_TRACE_FIELDS = frozenset(
    {
        "trace_id",
        "intent_id",
        "template_variant_id",
        "catalog_version",
        "source_event_ids",
        "channel",
        "audience_seat_ids",
        "admission_status",
        "suppress_reason",
        "elapsed_ms",
    }
)


def _positive_integer(value: object, *, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def validate_domain_transport_mapping(
    pairs: Iterable[tuple[object, object]],
) -> Mapping[int, int]:
    validated: dict[int, int] = {}
    for domain_value, transport_value in pairs:
        domain_seq = _positive_integer(domain_value, field_name="domain_seq")
        transport_seq = _positive_integer(transport_value, field_name="transport_seq")
        if domain_seq in validated:
            raise ValueError("duplicate domain_seq in domain_to_transport")
        validated[domain_seq] = transport_seq

    ordered_values = [validated[domain_seq] for domain_seq in sorted(validated)]
    if any(left >= right for left, right in pairwise(ordered_values)):
        raise ValueError("transport values must be strictly increasing")
    return MappingProxyType(validated)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _nearest_rank_p95(values: tuple[int, ...]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil(len(ordered) * 0.95) - 1
    return float(ordered[index])


def _max_transport_lag(mapping: Mapping[int, int]) -> int:
    return max(
        0,
        max(
            (domain_seq - transport_seq for domain_seq, transport_seq in mapping.items()),
            default=0,
        ),
    )


def _derive_domain_defaults(raw: dict[str, object]) -> None:
    mapping = raw.get("domain_to_transport", {})

    if "mapped_domain_slots" not in raw:
        if "template_admitted" in raw:
            raw["mapped_domain_slots"] = raw["template_admitted"]
        else:
            try:
                raw["mapped_domain_slots"] = len(cast(Mapping[object, object], mapping))
            except TypeError:
                raw["mapped_domain_slots"] = 0
    if "template_admitted" not in raw:
        raw["template_admitted"] = raw["mapped_domain_slots"]

    mapped = raw["mapped_domain_slots"]
    render_failed = raw.get("render_failed", 0)
    slot_suppressed = raw.get("slot_suppressed", 0)
    if (
        "completed_domain_slots" not in raw
        and type(mapped) is int
        and type(render_failed) is int
        and type(slot_suppressed) is int
    ):
        raw["completed_domain_slots"] = mapped + render_failed + slot_suppressed

    if isinstance(mapping, Mapping):
        try:
            validated = validate_domain_transport_mapping(mapping.items())
        except (AttributeError, ValueError):
            return
        raw.setdefault("max_domain_transport_lag", _max_transport_lag(validated))
        completed = raw.get("completed_domain_slots")
        if type(mapped) is int and type(completed) is int:
            raw.setdefault(
                "domain_transport_ratio",
                1.0 if completed == 0 else mapped / completed,
            )


class _DMMetricBase(StrictModel):
    eligible: int = Field(default=0, ge=0)
    template_admitted: int = Field(default=0, ge=0)
    render_failed: int = Field(default=0, ge=0)
    slot_suppressed: int = Field(default=0, ge=0)
    admission_ms: tuple[int, ...] = ()

    @field_validator("admission_ms", mode="before")
    @classmethod
    def parse_admission_ms(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json" and type(value) is list:
            return tuple(value)
        return value

    @field_validator("admission_ms")
    @classmethod
    def validate_admission_ms(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(elapsed_ms < 0 for elapsed_ms in value):
            raise ValueError("admission_ms entries must be non-negative")
        return value

    @property
    def consistent(self) -> bool:
        return self.eligible == self.completed

    @property
    def completed(self) -> int:
        return self.template_admitted + self.render_failed + self.slot_suppressed

    @property
    def template_admission_rate(self) -> float:
        return _ratio(self.template_admitted, self.completed)

    @property
    def render_failure_rate(self) -> float:
        return _ratio(self.render_failed, self.completed)

    @property
    def slot_suppressed_rate(self) -> float:
        return _ratio(self.slot_suppressed, self.completed)

    @property
    def admission_ms_p95(self) -> float:
        return _nearest_rank_p95(self.admission_ms)


class DMMetrics(_DMMetricBase):
    mapped_domain_slots: int = Field(default=0, ge=0)
    completed_domain_slots: int = Field(default=0, ge=0)
    domain_to_transport: Mapping[int, int] = Field(
        default_factory=lambda: MappingProxyType({}),
    )
    domain_transport_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    max_domain_transport_lag: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def derive_domain_metrics(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        raw = dict(cast(Mapping[str, object], data))
        _derive_domain_defaults(raw)
        return raw

    @field_validator("domain_to_transport", mode="after")
    @classmethod
    def freeze_domain_transport(cls, value: Mapping[int, int]) -> Mapping[int, int]:
        return validate_domain_transport_mapping(value.items())

    @field_validator("domain_to_transport", mode="before")
    @classmethod
    def parse_json_domain_transport(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if info.mode != "json" or not isinstance(value, Mapping):
            return value
        if all(type(key) is str and key.isdecimal() and str(int(key)) == key for key in value):
            return {int(key): item for key, item in value.items()}
        return value

    @field_serializer("domain_to_transport", when_used="always")
    def serialize_domain_transport(self, value: Mapping[int, int]) -> dict[int, int]:
        return dict(value)

    @model_validator(mode="after")
    def validate_conservation(self) -> Self:
        if self.template_admitted != self.mapped_domain_slots:
            raise ValueError("mapped_domain_slots must equal template_admitted")
        if len(self.domain_to_transport) != self.mapped_domain_slots:
            raise ValueError("domain_to_transport must equal mapped_domain_slots")
        if self.completed_domain_slots != (
            self.mapped_domain_slots + self.render_failed + self.slot_suppressed
        ):
            raise ValueError("domain slot conservation is violated")
        expected_lag = _max_transport_lag(self.domain_to_transport)
        expected_ratio = (
            1.0
            if self.completed_domain_slots == 0
            else self.mapped_domain_slots / self.completed_domain_slots
        )
        if self.max_domain_transport_lag != expected_lag:
            raise ValueError("max_domain_transport_lag does not match the mapping")
        if not math.isclose(
            self.domain_transport_ratio,
            expected_ratio,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("domain_transport_ratio does not match the mapping")
        return self


class DMAggregateMetrics(StrictModel):
    rooms: tuple[DMMetrics, ...] = ()

    @property
    def eligible(self) -> int:
        return sum(metric.eligible for metric in self.rooms)

    @property
    def template_admitted(self) -> int:
        return sum(metric.template_admitted for metric in self.rooms)

    @property
    def render_failed(self) -> int:
        return sum(metric.render_failed for metric in self.rooms)

    @property
    def slot_suppressed(self) -> int:
        return sum(metric.slot_suppressed for metric in self.rooms)

    @property
    def admission_ms(self) -> tuple[int, ...]:
        return tuple(elapsed_ms for metric in self.rooms for elapsed_ms in metric.admission_ms)

    @property
    def mapped_domain_slots(self) -> int:
        return sum(metric.mapped_domain_slots for metric in self.rooms)

    @property
    def completed_domain_slots(self) -> int:
        return self.mapped_domain_slots + self.render_failed + self.slot_suppressed

    @property
    def domain_transport_ratio(self) -> float:
        if self.completed_domain_slots == 0:
            return 1.0
        return self.mapped_domain_slots / self.completed_domain_slots

    @property
    def max_domain_transport_lag(self) -> int:
        return max(
            (metric.max_domain_transport_lag for metric in self.rooms),
            default=0,
        )

    @property
    def completed(self) -> int:
        return self.template_admitted + self.render_failed + self.slot_suppressed

    @property
    def consistent(self) -> bool:
        return self.eligible == self.completed

    @property
    def template_admission_rate(self) -> float:
        return _ratio(self.template_admitted, self.completed)

    @property
    def render_failure_rate(self) -> float:
        return _ratio(self.render_failed, self.completed)

    @property
    def slot_suppressed_rate(self) -> float:
        return _ratio(self.slot_suppressed, self.completed)

    @property
    def admission_ms_p95(self) -> float:
        return _nearest_rank_p95(self.admission_ms)


def _terminal_traces(traces: Iterable[DMTraceRecord]) -> tuple[DMTraceRecord, ...]:
    by_intent: dict[UUID, DMTraceRecord] = {}
    for trace in traces:
        current = by_intent.get(trace.intent_id)
        if current is None:
            by_intent[trace.intent_id] = trace
            continue
        if (
            current.suppress_reason == "duplicate_slot"
            and trace.suppress_reason != "duplicate_slot"
        ):
            by_intent[trace.intent_id] = trace
    return tuple(by_intent.values())


def build_room_dm_metrics(
    dm_trace: Iterable[DMTraceRecord],
    dm_transport_trace: Iterable[DMTraceRecord],
    domain_to_transport: Mapping[int, int],
) -> DMMetrics:
    terminal = _terminal_traces(
        trace for trace in dm_trace if trace.suppress_reason != "transport_failed"
    )
    mapped = validate_domain_transport_mapping(domain_to_transport.items())
    admitted = sum(trace.admission_status == "admitted" for trace in terminal)
    render_failed = sum(trace.admission_status == "failed" for trace in terminal)
    slot_suppressed = sum(trace.admission_status == "suppressed" for trace in terminal)
    if admitted != len(mapped):
        raise ValueError("admitted trace count must equal mapped domain slots")

    eligible_ids = {trace.intent_id for trace in terminal}
    eligible_ids.update(trace.intent_id for trace in dm_transport_trace)
    return DMMetrics(
        eligible=len(eligible_ids),
        template_admitted=admitted,
        render_failed=render_failed,
        slot_suppressed=slot_suppressed,
        admission_ms=tuple(trace.elapsed_ms for trace in terminal),
        mapped_domain_slots=len(mapped),
        completed_domain_slots=len(mapped) + render_failed + slot_suppressed,
        domain_to_transport=mapped,
    )


def aggregate_dm_metrics(metrics: Iterable[DMMetrics]) -> DMAggregateMetrics:
    return DMAggregateMetrics(rooms=tuple(metrics))


def clip_dm_trace(*trace_groups: Iterable[DMTraceRecord]) -> tuple[dict[str, JsonValue], ...]:
    return tuple(
        cast(
            dict[str, JsonValue],
            trace.model_dump(mode="json", include=set(_TRACE_FIELDS)),
        )
        for trace_group in trace_groups
        for trace in trace_group
    )
