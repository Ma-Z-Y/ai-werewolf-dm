from __future__ import annotations

import json
from types import MappingProxyType
from uuid import uuid4

import pytest
from pydantic import ValidationError

from werewolf_dm.application.dm_contracts import DMTraceRecord
from werewolf_dm.application.dm_metrics import (
    DMMetrics,
    aggregate_dm_metrics,
    build_room_dm_metrics,
    validate_domain_transport_mapping,
)


def _trace(
    *,
    intent_id: object | None = None,
    admission_status: str = "admitted",
    suppress_reason: str | None = None,
    elapsed_ms: int = 1,
) -> DMTraceRecord:
    return DMTraceRecord(
        trace_id=uuid4(),
        intent_id=uuid4() if intent_id is None else intent_id,
        template_variant_id="s4-phase-notice-neutral-v1",
        catalog_version="s4-template-v1",
        source_event_ids=(uuid4(),),
        channel="public",
        audience_seat_ids=(),
        admission_status=admission_status,
        suppress_reason=suppress_reason,
        elapsed_ms=elapsed_ms,
    )


def test_template_rates_are_mutually_exclusive() -> None:
    metrics = DMMetrics(
        eligible=10,
        template_admitted=7,
        render_failed=1,
        slot_suppressed=2,
        domain_to_transport={1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7},
    )

    assert metrics.completed == 10
    assert metrics.template_admission_rate == 0.7
    assert metrics.render_failure_rate == 0.1
    assert metrics.slot_suppressed_rate == 0.2
    assert (
        metrics.template_admission_rate + metrics.render_failure_rate + metrics.slot_suppressed_rate
        == 1.0
    )


def test_empty_template_metrics_close_rates_and_domain_ratio() -> None:
    metrics = DMMetrics()

    assert metrics.completed == 0
    assert metrics.template_admission_rate == 0.0
    assert metrics.render_failure_rate == 0.0
    assert metrics.slot_suppressed_rate == 0.0
    assert metrics.domain_transport_ratio == 1.0
    assert metrics.max_domain_transport_lag == 0


def test_domain_transport_mapping_is_conserved() -> None:
    metrics = DMMetrics(
        completed_domain_slots=5,
        mapped_domain_slots=4,
        render_failed=0,
        slot_suppressed=1,
        domain_to_transport={1: 1, 2: 2, 4: 3, 5: 4},
    )

    assert metrics.domain_transport_ratio == 0.8
    assert metrics.max_domain_transport_lag == 1
    assert metrics.completed_domain_slots == (
        metrics.mapped_domain_slots + metrics.render_failed + metrics.slot_suppressed
    )
    assert list(metrics.domain_to_transport.values()) == [1, 2, 3, 4]


def test_domain_transport_mapping_allows_wire_gaps_but_rejects_regression() -> None:
    assert dict(validate_domain_transport_mapping(((1, 1), (2, 3)))) == {1: 1, 2: 3}

    with pytest.raises(ValueError, match="strictly increasing"):
        validate_domain_transport_mapping(((1, 2), (2, 2)))

    with pytest.raises(ValueError, match="strictly increasing"):
        validate_domain_transport_mapping(((1, 3), (2, 2)))


def test_domain_transport_mapping_rejects_duplicate_and_non_wire_values() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_domain_transport_mapping(((1, 1), (1, 2)))

    for invalid in (True, 0, -1, "1", 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            validate_domain_transport_mapping(((1, invalid),))

    with pytest.raises(ValidationError):
        DMMetrics(domain_to_transport={1: 1, 2: 1})


@pytest.mark.parametrize(
    "payload",
    [
        {"mapped_domain_slots": 1, "domain_to_transport": {1: 1, 2: 2}},
        {"mapped_domain_slots": 0, "domain_to_transport": {1: 1}},
        {
            "domain_to_transport": {},
            "render_failed": 1,
            "completed_domain_slots": 1,
            "domain_transport_ratio": 1.0,
        },
        {"max_domain_transport_lag": 5},
        {"template_admitted": 1},
    ],
)
def test_metrics_reject_mapping_and_empty_state_invariant_bypasses(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DMMetrics(**payload)


def test_metrics_reject_explicit_null_mapping_and_negative_latency() -> None:
    with pytest.raises(ValidationError):
        DMMetrics.model_validate_json('{"domain_to_transport": null}')

    with pytest.raises(ValidationError):
        DMMetrics(admission_ms=(-1,))


def test_metrics_mapping_supports_copy_revalidate_and_json_serialization() -> None:
    metrics = DMMetrics(domain_to_transport={1: 1, 3: 2})

    copied = metrics.model_copy()
    revalidated = DMMetrics.revalidate(metrics)
    serialized = metrics.model_dump(mode="json")

    assert dict(copied.domain_to_transport) == {1: 1, 3: 2}
    assert dict(revalidated.domain_to_transport) == {1: 1, 3: 2}
    assert json.loads(json.dumps(serialized))["domain_to_transport"] == {
        "1": 1,
        "3": 2,
    }


def test_dm_metrics_serialization_round_trip_preserves_invariants() -> None:
    metrics = DMMetrics(
        eligible=2,
        template_admitted=1,
        render_failed=1,
        admission_ms=(3, 5),
        domain_to_transport={1: 1},
    )

    restored = DMMetrics.model_validate_json(metrics.model_dump_json())

    assert restored == metrics
    assert restored.consistent is True
    assert restored.completed == restored.eligible
    assert restored.domain_transport_ratio == 0.5


def test_dm_metrics_json_normalization_does_not_relax_python_validation() -> None:
    with pytest.raises(ValidationError):
        DMMetrics(admission_ms=[1])

    with pytest.raises(ValidationError):
        DMMetrics.model_validate({"domain_to_transport": {"1": 1}})


def test_transport_failed_is_distinct_from_slot_suppressed() -> None:
    intent_id = uuid4()
    admitted = _trace(intent_id=intent_id, elapsed_ms=4)
    transport_failed = _trace(
        intent_id=intent_id,
        admission_status="suppressed",
        suppress_reason="transport_failed",
        elapsed_ms=9,
    )

    metrics = build_room_dm_metrics(
        (admitted, transport_failed),
        (transport_failed,),
        {1: 1},
    )

    assert metrics.eligible == 1
    assert metrics.template_admitted == 1
    assert metrics.render_failed == 0
    assert metrics.slot_suppressed == 0
    assert metrics.completed == 1
    assert metrics.admission_ms == (4,)


def test_transport_failed_and_slot_suppressed_are_mutually_exclusive() -> None:
    admitted_intent = uuid4()
    suppressed_intent = uuid4()
    transport_failed = _trace(
        intent_id=admitted_intent,
        admission_status="suppressed",
        suppress_reason="transport_failed",
    )
    admission_suppressed = _trace(
        intent_id=suppressed_intent,
        admission_status="suppressed",
        suppress_reason="admission_timeout",
    )

    metrics = build_room_dm_metrics(
        (
            _trace(intent_id=admitted_intent),
            transport_failed,
            admission_suppressed,
        ),
        (transport_failed,),
        {1: 1},
    )

    assert metrics.template_admitted == 1
    assert metrics.slot_suppressed == 1
    assert metrics.completed == 2
    assert metrics.consistent is True


def test_dm_metrics_nested_values_are_immutable() -> None:
    metrics = DMMetrics(domain_to_transport={1: 1, 2: 3})

    assert isinstance(metrics.domain_to_transport, MappingProxyType)
    assert all(type(value) is int for value in metrics.domain_to_transport.values())
    with pytest.raises(TypeError):
        metrics.domain_to_transport[3] = 4


def test_duplicate_slot_does_not_double_count_a_terminal_slot() -> None:
    intent_id = uuid4()
    admitted = _trace(intent_id=intent_id)
    duplicate = _trace(
        intent_id=intent_id,
        admission_status="suppressed",
        suppress_reason="duplicate_slot",
    )

    metrics = build_room_dm_metrics((admitted, duplicate), (), {1: 1})

    assert metrics.template_admitted == 1
    assert metrics.slot_suppressed == 0
    assert metrics.completed == 1


def test_aggregate_metrics_preserves_global_ratio_and_max_lag() -> None:
    first = DMMetrics(
        template_admitted=2,
        domain_to_transport={1: 1, 2: 2},
    )
    second = DMMetrics(
        template_admitted=1,
        render_failed=1,
        domain_to_transport={1: 2},
    )

    metrics = aggregate_dm_metrics((first, second))

    assert metrics.template_admitted == 3
    assert metrics.render_failed == 1
    assert metrics.completed == 4
    assert metrics.mapped_domain_slots == 3
    assert metrics.completed_domain_slots == 4
    assert metrics.domain_transport_ratio == 0.75
    assert metrics.max_domain_transport_lag == 0
