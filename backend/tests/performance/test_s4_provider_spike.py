from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from tools.s4_provider_spike import (  # noqa: E402
    _is_spike_payload,
    _tier_sequence,
    summarize_spike,
    write_spike_report,
)


def test_warmup_schedule_does_not_require_measured_tier_quota() -> None:
    schedule = _tier_sequence(["short", "mid", "long"], 20, minimum_per_tier=0)

    assert len(schedule) == 20


def test_spike_payload_accepts_safe_underscore_phrase_ids() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"schema_version":"s4-p0",'
                        '"prefix_phrase_id":"pf_dawn_calm_01",'
                        '"suffix_phrase_id":"sf_ready_next_01",'
                        '"fact_refs":["day-dawn"],'
                        '"claimed_refs":[],'
                        '"style":"neutral"}'
                    )
                }
            }
        ]
    }

    assert _is_spike_payload(payload)


def test_spike_rates_include_failed_calls(tmp_path: Path) -> None:
    results = [
        {"ok": True, "latency_ms": 10.0},
        {"ok": False, "latency_ms": 20.0, "error": "refusal"},
    ]

    report = summarize_spike("local", "model", results)

    candidate = report["candidates"][0]
    assert candidate["provider_failure_rate"] == 0.5
    assert candidate["spike_acceptance_rate"] == 0.5
    assert "samples" not in candidate["total"]
    write_spike_report(tmp_path / "report.json", report)
    assert (tmp_path / "report.json").exists()
