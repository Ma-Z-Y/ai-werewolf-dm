from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
BUDGET_PATH = FIXTURE_DIR / "s4_provider_budget.json"
REPORT_PATH = FIXTURE_DIR / "s4_provider_spike_report.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_provider_budget_freezes_template_only_after_failed_spike() -> None:
    budget = _load_json(BUDGET_PATH)
    report = _load_json(REPORT_PATH)

    assert report["selected_provider"] == "none"
    assert budget["decision"] == "template-only"
    assert budget["provider"] == report["selected_provider"]
    assert budget["provider"] == "none"
    assert budget["llm_enabled"] is False

    assert budget["llm_cancel_ms"] == 1500
    assert budget["fallback_deadline_ms"] == 1600
    assert budget["absolute_admission_ms"] == 2000
    assert budget["runtime_hit_rate_min"] == 0.50
    assert budget["runtime_hit_rate_scope"] == "future-provider-reentry"

    assert budget["d3_input"]["provider_boundary"] == "disabled-for-s4"
    assert budget["d3_input"]["provider_jobs"] == "not-created"
    assert budget["d4_input"]["default_source"] == "template"
    assert budget["d4_input"]["fallback_path"] == "template"
    assert budget["d4_input"]["player_visible_retry"] is False

    assert budget["downstream_impact"]["s4_implementation_plan"] == ("requires-rebaseline")
    assert budget["downstream_impact"]["s4_04_provider_adapter"] == ("removed-from-s4")
    assert budget["downstream_impact"]["s4_09_real_provider_evidence"] == ("removed-from-s4")
    assert budget["downstream_impact"]["next_task"] == "S4-P0-01c"


def test_provider_budget_matches_every_failed_spike_candidate() -> None:
    budget = _load_json(BUDGET_PATH)
    report = _load_json(REPORT_PATH)

    candidates = report["candidates"]
    assert 2 <= len(candidates) <= 3
    assert all(candidate["meets_thresholds"] is False for candidate in candidates)

    observed = {entry["provider"]: entry for entry in budget["decision_basis"]["observed"]}
    assert set(observed) == {candidate["provider"] for candidate in candidates}
    environment = budget["spike_environment"]
    assert environment["hardware"] == candidates[0]["hardware"]
    assert environment["network"] == candidates[0]["network"]
    assert environment["prompt_version"] == candidates[0]["prompt_version"]
    assert environment["candidate_samples"] == {
        "total": candidates[0]["total"]["count"],
        "cold": candidates[0]["cold"]["count"],
        "warm": candidates[0]["warm"]["count"],
    }
    recorded_failures = {
        (entry["provider"], entry["phase"], entry["tier"], entry["prompt_id"]): (
            entry["latency_ms"],
            entry["error"],
        )
        for entry in environment["failure_samples"]
    }
    report_failures = {}

    for candidate in candidates:
        entry = observed[candidate["provider"]]
        assert entry["p99_ms"] == candidate["total"]["p99_ms"]
        assert entry["maximum_ms"] == candidate["total"]["maximum_ms"]
        assert entry["provider_failure_rate"] == candidate["provider_failure_rate"]
        assert entry["spike_acceptance_rate"] == candidate["spike_acceptance_rate"]
        assert (
            candidate["total"]["p99_ms"] >= 1200
            or candidate["total"]["maximum_ms"] >= 1500
            or candidate["spike_acceptance_rate"] < 0.50
        )
        failure = candidate["failures"][0]
        report_failures[
            (candidate["provider"], failure["phase"], failure["tier"], failure["prompt_id"])
        ] = (
            failure["latency_ms"],
            failure["error"],
        )

    assert recorded_failures == report_failures
