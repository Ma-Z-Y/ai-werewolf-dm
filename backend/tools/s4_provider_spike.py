from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import platform
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_PROMPT_VERSION = "s4-prompts.v1"
REPORT_VERSION = "s4-spike.v1"
ALLOWED_STYLES = frozenset({"neutral", "warm"})
PHRASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class SpikePreflightError(RuntimeError):
    """Raised when the local provider configuration cannot run the spike."""


@dataclass(frozen=True, slots=True)
class ProviderSpikeConfig:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    network: str = "internet"
    timeout_seconds: float = 15.0
    cold_count: int = 100
    warm_count: int = 100
    warmup_count: int = 20
    p99_limit_ms: float = 1200.0
    maximum_limit_ms: float = 1500.0
    failure_rate_limit: float = 0.05


@dataclass(frozen=True, slots=True)
class SpikeResult:
    ok: bool
    accepted: bool
    latency_ms: float
    phase: str
    tier: str
    prompt_id: str
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": self.ok,
            "accepted": self.accepted,
            "latency_ms": self.latency_ms,
            "phase": self.phase,
            "tier": self.tier,
            "prompt_id": self.prompt_id,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


def summarize_spike(
    provider: str,
    model: str,
    results: Sequence[Mapping[str, object]],
    *,
    hardware: str = "unspecified",
    network: str = "test",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    p99_limit_ms: float = 1200.0,
    maximum_limit_ms: float = 1500.0,
    failure_rate_limit: float = 0.05,
) -> dict[str, Any]:
    total = _summarize_group(results)
    cold = _summarize_group(_phase(results, "cold"))
    warm = _summarize_group(_phase(results, "warm"))
    tiers = [
        {"name": tier, **_summarize_group(_tier(results, tier))}
        for tier in sorted({str(result.get("tier", "unknown")) for result in results})
    ]
    total_count = total["count"]
    failed_count = sum(1 for result in results if not bool(result.get("ok")))
    accepted_count = sum(1 for result in results if _is_accepted(result))
    provider_failure_rate = failed_count / total_count if total_count else 0.0
    acceptance_rate = accepted_count / total_count if total_count else 0.0
    meets_thresholds = (
        total_count > 0
        and total["p99_ms"] < p99_limit_ms
        and total["maximum_ms"] < maximum_limit_ms
        and provider_failure_rate < failure_rate_limit
    )
    candidate: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "hardware": hardware,
        "network": network,
        "prompt_version": prompt_version,
        "total": total,
        "cold": cold,
        "warm": warm,
        "provider_failure_rate": provider_failure_rate,
        "spike_acceptance_rate": acceptance_rate,
        "tiers": tiers,
        "failures": _failed_samples(results),
        "meets_thresholds": meets_thresholds,
    }
    return {
        "selected_provider": provider if meets_thresholds else "none",
        "report_version": REPORT_VERSION,
        "candidates": [candidate],
    }


def write_spike_report(path: Path, report: Mapping[str, object]) -> None:
    _validate_report(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_prompt_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("prompt fixture must contain a JSON object")
    prompt_version = payload.get("prompt_version")
    system_prompt = payload.get("system")
    tiers = payload.get("tiers")
    if not isinstance(prompt_version, str) or not prompt_version:
        raise ValueError("prompt fixture is missing prompt_version")
    if not isinstance(system_prompt, str) or not system_prompt:
        raise ValueError("prompt fixture is missing system")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("prompt fixture is missing tiers")
    validated_tiers: list[dict[str, str]] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            raise ValueError("prompt tier must be an object")
        name = tier.get("name")
        prompt = tier.get("prompt")
        if not isinstance(name, str) or not name:
            raise ValueError("prompt tier is missing name")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"prompt tier {name} is missing prompt")
        validated_tiers.append({"name": name, "prompt": prompt})
    return {
        "prompt_version": prompt_version,
        "system": system_prompt,
        "tiers": validated_tiers,
    }


def candidate_configs_from_environment(
    *,
    cold_count: int = 100,
    warm_count: int = 100,
    warmup_count: int = 20,
    timeout_seconds: float = 15.0,
) -> list[ProviderSpikeConfig]:
    raw_candidates = os.environ.get("S4_SPIKE_CANDIDATES_JSON")
    candidates: list[ProviderSpikeConfig] = []
    if raw_candidates:
        payload = json.loads(raw_candidates)
        if not isinstance(payload, list):
            raise SpikePreflightError("S4_SPIKE_CANDIDATES_JSON must be a JSON array")
        for item in payload:
            if not isinstance(item, dict):
                raise SpikePreflightError("each candidate must be a JSON object")
            candidates.append(
                _config_from_mapping(
                    item,
                    cold_count=cold_count,
                    warm_count=warm_count,
                    warmup_count=warmup_count,
                    timeout_seconds=timeout_seconds,
                )
            )
    else:
        candidates.extend(
            _default_candidates(
                cold_count=cold_count,
                warm_count=warm_count,
                warmup_count=warmup_count,
                timeout_seconds=timeout_seconds,
            )
        )
    if not 2 <= len(candidates) <= 3:
        raise SpikePreflightError("configure 2-3 provider candidates")
    return candidates


def run_spike(
    config: ProviderSpikeConfig,
    prompts: Mapping[str, Any],
) -> dict[str, Any]:
    tier_names = [str(tier["name"]) for tier in prompts["tiers"]]
    system_prompt = str(prompts["system"])
    prompt_version = str(prompts["prompt_version"])
    warmups = _tier_sequence(
        tier_names,
        config.warmup_count,
        minimum_per_tier=0,
    )
    cold_tiers = _tier_sequence(tier_names, config.cold_count)
    warm_tiers = _tier_sequence(tier_names, config.warm_count)
    results: list[dict[str, object]] = []
    warm_connection: http.client.HTTPConnection | None = None
    try:
        for index, tier in enumerate(warmups):
            prompt = _prompt_for_tier(prompts, tier, index)
            warm_connection = _call_with_reconnect(
                config,
                warm_connection,
                system_prompt,
                prompt,
                tier,
                f"warmup-{index + 1}",
            )[1]
        for index, tier in enumerate(cold_tiers):
            prompt = _prompt_for_tier(prompts, tier, index)
            result, _ = _call_once(
                config,
                system_prompt,
                prompt,
                tier,
                f"cold-{index + 1}",
                phase="cold",
            )
            results.append(result.as_dict())
        for index, tier in enumerate(warm_tiers):
            prompt = _prompt_for_tier(prompts, tier, index)
            result, warm_connection = _call_with_reconnect(
                config,
                warm_connection,
                system_prompt,
                prompt,
                tier,
                f"warm-{index + 1}",
                phase="warm",
            )
            results.append(result.as_dict())
    finally:
        if warm_connection is not None:
            warm_connection.close()
    return summarize_spike(
        config.provider,
        config.model,
        results,
        hardware=_hardware_description(),
        network=config.network,
        prompt_version=prompt_version,
        p99_limit_ms=config.p99_limit_ms,
        maximum_limit_ms=config.maximum_limit_ms,
        failure_rate_limit=config.failure_rate_limit,
    )


def _config_from_mapping(
    item: Mapping[str, object],
    *,
    cold_count: int,
    warm_count: int,
    warmup_count: int,
    timeout_seconds: float,
) -> ProviderSpikeConfig:
    provider = _required_string(item, "provider")
    model = _required_string(item, "model")
    base_url_env = _required_string(item, "base_url_env")
    api_key_env = _required_string(item, "api_key_env")
    network = str(item.get("network", "internet"))
    base_url = os.environ.get(base_url_env)
    api_key = os.environ.get(api_key_env)
    if not base_url:
        raise SpikePreflightError(f"{provider}: missing environment variable {base_url_env}")
    if not api_key:
        raise SpikePreflightError(f"{provider}: missing environment variable {api_key_env}")
    return ProviderSpikeConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        network=network,
        timeout_seconds=timeout_seconds,
        cold_count=cold_count,
        warm_count=warm_count,
        warmup_count=warmup_count,
    )


def _default_candidates(
    *,
    cold_count: int,
    warm_count: int,
    warmup_count: int,
    timeout_seconds: float,
) -> list[ProviderSpikeConfig]:
    definitions = (
        {
            "provider": "deepseek-flash",
            "model": "deepseek-flash",
            "base_url_env": "OPENAI_BASE_URL",
            "api_key_env": "OPENAI_API_KEY",
            "network": "internet",
        },
        {
            "provider": "deepseek-v4-pro",
            "model": "deepseek-v4-pro",
            "base_url_env": "CODEX_PLUS_OPENAI_BASE_URL",
            "api_key_env": "CODEX_PLUS_OPENAI_API_KEY",
            "network": "internet",
        },
    )
    return [
        _config_from_mapping(
            item,
            cold_count=cold_count,
            warm_count=warm_count,
            warmup_count=warmup_count,
            timeout_seconds=timeout_seconds,
        )
        for item in definitions
        if item["base_url_env"] in os.environ and item["api_key_env"] in os.environ
    ]


def _call_with_reconnect(
    config: ProviderSpikeConfig,
    connection: http.client.HTTPConnection | None,
    system_prompt: str,
    prompt: str,
    tier: str,
    prompt_id: str,
    *,
    phase: str = "warmup",
) -> tuple[SpikeResult, http.client.HTTPConnection]:
    active = connection if connection is not None else _new_connection(config)
    result = _call_on_connection(
        config,
        active,
        system_prompt,
        prompt,
        tier,
        prompt_id,
        phase,
    )
    if not result.ok:
        active.close()
        active = _new_connection(config)
    return result, active


def _call_once(
    config: ProviderSpikeConfig,
    system_prompt: str,
    prompt: str,
    tier: str,
    prompt_id: str,
    *,
    phase: str,
) -> tuple[SpikeResult, http.client.HTTPConnection]:
    connection = _new_connection(config)
    try:
        return (
            _call_on_connection(
                config,
                connection,
                system_prompt,
                prompt,
                tier,
                prompt_id,
                phase,
            ),
            connection,
        )
    finally:
        connection.close()


def _call_on_connection(
    config: ProviderSpikeConfig,
    connection: http.client.HTTPConnection,
    system_prompt: str,
    prompt: str,
    tier: str,
    prompt_id: str,
    phase: str,
) -> SpikeResult:
    started = time.perf_counter_ns()
    try:
        _scheme, _host, _port, path = _endpoint(config.base_url)
        body = json.dumps(
            {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 160,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        raw = response.read()
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000
        if response.status != 200:
            return SpikeResult(
                ok=False,
                accepted=False,
                latency_ms=latency_ms,
                phase=phase,
                tier=tier,
                prompt_id=prompt_id,
                error=f"http_{response.status}",
            )
        payload = json.loads(raw.decode("utf-8"))
        accepted = _is_spike_payload(payload)
        return SpikeResult(
            ok=True,
            accepted=accepted,
            latency_ms=latency_ms,
            phase=phase,
            tier=tier,
            prompt_id=prompt_id,
            error=None if accepted else "schema",
        )
    except Exception as exc:
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000
        return SpikeResult(
            ok=False,
            accepted=False,
            latency_ms=latency_ms,
            phase=phase,
            tier=tier,
            prompt_id=prompt_id,
            error=type(exc).__name__,
        )


def _new_connection(config: ProviderSpikeConfig) -> http.client.HTTPConnection:
    scheme, host, port, _path = _endpoint(config.base_url)
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=config.timeout_seconds)
    return http.client.HTTPConnection(host, port, timeout=config.timeout_seconds)


def _endpoint(base_url: str) -> tuple[str, str, int | None, str]:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SpikePreflightError("base URL must be an absolute http(s) URL")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    elif path.endswith("/v1"):
        endpoint_path = f"{path}/chat/completions"
    elif path:
        endpoint_path = f"{path}/v1/chat/completions"
    else:
        endpoint_path = "/v1/chat/completions"
    return parsed.scheme, parsed.hostname, parsed.port, endpoint_path


def _is_spike_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    if not isinstance(first, dict):
        return False
    message = first.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(result, dict):
        return False
    if result.get("schema_version") != "s4-p0":
        return False
    if not _valid_phrase_id(result.get("prefix_phrase_id")):
        return False
    if not _valid_phrase_id(result.get("suffix_phrase_id")):
        return False
    fact_refs = result.get("fact_refs")
    if not isinstance(fact_refs, list) or not fact_refs:
        return False
    if not all(isinstance(ref, str) and ref for ref in fact_refs):
        return False
    if result.get("claimed_refs") != []:
        return False
    return result.get("style") in ALLOWED_STYLES


def _valid_phrase_id(value: object) -> bool:
    return isinstance(value, str) and PHRASE_ID_PATTERN.fullmatch(value) is not None


def _summarize_group(
    results: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    latencies = [_latency_ms(result) for result in results]
    return {
        "count": len(results),
        "p99_ms": _nearest_rank(latencies, 0.99),
        "maximum_ms": max(latencies, default=0.0),
    }


def _failed_samples(
    results: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for result in results:
        if bool(result.get("ok")):
            continue
        failures.append(
            {
                key: result[key]
                for key in ("phase", "tier", "prompt_id", "latency_ms", "error")
                if key in result
            }
        )
    return failures


def _is_accepted(result: Mapping[str, object]) -> bool:
    if not bool(result.get("ok")):
        return False
    return bool(result.get("accepted", True))


def _latency_ms(result: Mapping[str, object]) -> float:
    value = result.get("latency_ms", 0.0)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(1, math.ceil(len(ordered) * quantile)) - 1
    return ordered[index]


def _phase(
    results: Sequence[Mapping[str, object]],
    phase: str,
) -> list[Mapping[str, object]]:
    return [result for result in results if str(result.get("phase", "total")) == phase]


def _tier(
    results: Sequence[Mapping[str, object]],
    tier: str,
) -> list[Mapping[str, object]]:
    return [result for result in results if str(result.get("tier", "unknown")) == tier]


def _tier_sequence(
    tier_names: Sequence[str],
    count: int,
    *,
    minimum_per_tier: int = 30,
) -> list[str]:
    if count < minimum_per_tier * len(tier_names):
        raise ValueError("each prompt tier needs at least 30 measured calls")
    sequence = [tier for tier in tier_names for _ in range(minimum_per_tier)]
    sequence = sequence[:count]
    index = 0
    while len(sequence) < count:
        sequence.append(tier_names[index % len(tier_names)])
        index += 1
    return sequence


def _prompt_for_tier(
    prompts: Mapping[str, Any],
    tier: str,
    index: int,
) -> str:
    for candidate in prompts["tiers"]:
        if isinstance(candidate, dict) and candidate.get("name") == tier:
            return str(candidate["prompt"])
    raise ValueError(f"unknown prompt tier: {tier}")


def _hardware_description() -> str:
    processor = platform.processor() or platform.machine() or "unknown CPU"
    cores = os.cpu_count() or 0
    return f"{platform.system()} {platform.release()} | {processor} | cores={cores}"


def _required_string(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise SpikePreflightError(f"candidate is missing {key}")
    return value


def _validate_report(report: Mapping[str, object]) -> None:
    selected_provider = report.get("selected_provider")
    report_version = report.get("report_version")
    candidates = report.get("candidates")
    if not isinstance(selected_provider, str) or not selected_provider:
        raise ValueError("report is missing selected_provider")
    if report_version != REPORT_VERSION:
        raise ValueError("report is missing the frozen report_version")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("report must contain at least one candidate")
    required = (
        "provider",
        "model",
        "hardware",
        "network",
        "prompt_version",
        "total",
        "cold",
        "warm",
        "provider_failure_rate",
        "spike_acceptance_rate",
        "tiers",
        "failures",
    )
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        for key in required:
            if key not in candidate:
                raise ValueError(f"candidate {index} is missing {key}")
        for key in ("provider", "model", "hardware", "network", "prompt_version"):
            value = candidate[key]
            if not isinstance(value, str) or not value:
                raise ValueError(f"candidate {index} has an empty {key}")


def _select_provider(reports: Sequence[Mapping[str, Any]]) -> str:
    candidates = [candidate for report in reports for candidate in report["candidates"]]
    passing = [candidate for candidate in candidates if bool(candidate["meets_thresholds"])]
    if not passing:
        return "none"
    selected = max(
        passing,
        key=lambda candidate: (
            float(candidate["spike_acceptance_rate"]),
            -float(candidate["total"]["p99_ms"]),
        ),
    )
    return str(selected["provider"])


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the S4 provider selection spike.")
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cold-count", type=int, default=100)
    parser.add_argument("--warm-count", type=int, default=100)
    parser.add_argument("--warmup-count", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        prompts = load_prompt_fixture(args.prompts)
        configs = candidate_configs_from_environment(
            cold_count=args.cold_count,
            warm_count=args.warm_count,
            warmup_count=args.warmup_count,
            timeout_seconds=args.timeout_seconds,
        )
    except (SpikePreflightError, ValueError, json.JSONDecodeError) as exc:
        print(f"preflight failure: {exc}", file=sys.stderr)
        return 2
    reports = [run_spike(config, prompts) for config in configs]
    report: dict[str, Any] = {
        "selected_provider": _select_provider(reports),
        "report_version": REPORT_VERSION,
        "candidates": [
            candidate
            for candidate_report in reports
            for candidate in candidate_report["candidates"]
        ],
    }
    write_spike_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["selected_provider"] != "none" else 1


if __name__ == "__main__":
    raise SystemExit(main())
