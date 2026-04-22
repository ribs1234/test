#!/usr/bin/env python3
"""Offline evaluation harness for Einstein retrieval confidence and top-k quality."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from confluence_search_agent import ConfluenceSearchAgent, ConfluenceSearchError, SearchResult


@dataclass
class EvalCaseResult:
    query: str
    expected_any: list[str]
    has_expectation: bool
    top_titles: list[str]
    top_urls: list[str]
    confidence: float
    confidence_gap: float
    intent_label: str
    low_confidence: bool
    top1_hit: bool
    top3_hit: bool


def _normalize(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def _contains_any(haystack: str, needles: list[str]) -> bool:
    normalized_h = _normalize(haystack)
    for needle in needles:
        if _normalize(needle) and _normalize(needle) in normalized_h:
            return True
    return False


def _load_cases(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Evaluation file must be a JSON array of cases.")
    return [item for item in data if isinstance(item, dict)]


def _agent_from_env() -> ConfluenceSearchAgent:
    return ConfluenceSearchAgent(
        base_url=os.getenv("CONFLUENCE_BASE_URL", ""),
        personal_access_token=os.getenv("CONFLUENCE_PERSONAL_ACCESS_TOKEN")
        or os.getenv("CONFLUENCE_PAT"),
        email=os.getenv("CONFLUENCE_EMAIL"),
        api_token=os.getenv("CONFLUENCE_API_TOKEN"),
        bearer_token=os.getenv("CONFLUENCE_BEARER_TOKEN"),
        timeout=int(os.getenv("CONFLUENCE_TIMEOUT", "20")),
        summarizer_backend=os.getenv("CONFLUENCE_SUMMARIZER_BACKEND", "auto"),
        summarizer_api_key=os.getenv("CONFLUENCE_SUMMARIZER_API_KEY"),
        summarizer_model=os.getenv("CONFLUENCE_SUMMARIZER_MODEL"),
        summarizer_api_base=os.getenv(
            "CONFLUENCE_SUMMARIZER_API_BASE", "https://api.openai.com/v1"
        ),
        summarizer_max_results=int(os.getenv("CONFLUENCE_SUMMARIZER_MAX_RESULTS", "5")),
        candidate_pool_multiplier=int(os.getenv("CONFLUENCE_CANDIDATE_POOL_MULTIPLIER", "4")),
        candidate_pool_cap=int(os.getenv("CONFLUENCE_CANDIDATE_POOL_CAP", "50")),
    )


def _result_hit(item: SearchResult, expected_any: list[str]) -> bool:
    return _contains_any(item.title, expected_any) or _contains_any(item.url, expected_any)


def _coerce_expected_any(case: dict[str, Any]) -> list[str]:
    preferred = case.get("expected_any")
    legacy = case.get("expected_titles")
    source = preferred if isinstance(preferred, list) else legacy if isinstance(legacy, list) else []
    out: list[str] = []
    for item in source:
        value = str(item or "").strip()
        if value:
            out.append(value)
    return out


def _coerce_limit(case: dict[str, Any], default_limit: int) -> int:
    raw = case.get("limit", default_limit)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = default_limit
    return max(1, min(parsed, 50))


def _coerce_space_key(case: dict[str, Any], default_space_key: str | None) -> str | None:
    raw = case.get("space_key", default_space_key)
    value = str(raw or "").strip().upper()
    return value or None


def evaluate_case(
    agent: ConfluenceSearchAgent,
    *,
    query: str,
    expected_any: list[str],
    limit: int,
    space_key: str | None,
) -> EvalCaseResult:
    results = agent.search(query=query, limit=limit, space_key=space_key)
    top_titles = [item.title for item in results[:3]]
    top_urls = [item.url for item in results[:3]]

    has_expectation = bool(expected_any)
    top1_hit = bool(has_expectation and results and _result_hit(results[0], expected_any))
    top3_hit = bool(has_expectation and any(_result_hit(item, expected_any) for item in results[:3]))
    ranking = agent.last_ranking()

    return EvalCaseResult(
        query=query,
        expected_any=expected_any,
        has_expectation=has_expectation,
        top_titles=top_titles,
        top_urls=top_urls,
        confidence=(ranking.confidence if ranking else 0.0),
        confidence_gap=(ranking.score_gap if ranking else 0.0),
        intent_label=(ranking.intent_label if ranking else "unknown"),
        low_confidence=(ranking.low_confidence if ranking else True),
        top1_hit=top1_hit,
        top3_hit=top3_hit,
    )


def _print_summary(results: list[EvalCaseResult]) -> None:
    if not results:
        print("No evaluation cases were run.")
        return
    total = len(results)
    with_expectations = [item for item in results if item.has_expectation]
    top1 = sum(1 for item in with_expectations if item.top1_hit)
    top3 = sum(1 for item in with_expectations if item.top3_hit)
    avg_conf = sum(item.confidence for item in results) / total
    avg_gap = sum(item.confidence_gap for item in results) / total
    low_conf_count = sum(1 for item in results if item.low_confidence)

    print(f"Cases: {total}")
    print(f"Cases with expectations: {len(with_expectations)}")
    if with_expectations:
        denom = len(with_expectations)
        print(f"Top-1 hit rate: {top1}/{denom} ({(top1/denom)*100:.1f}%)")
        print(f"Top-3 hit rate: {top3}/{denom} ({(top3/denom)*100:.1f}%)")
    print(f"Average confidence: {avg_conf:.3f}")
    print(f"Average confidence gap: {avg_gap:.3f}")
    print(f"Low-confidence predictions: {low_conf_count}/{total}")
    if with_expectations:
        hit_conf = [item.confidence for item in with_expectations if item.top3_hit]
        miss_conf = [item.confidence for item in with_expectations if not item.top3_hit]
        if hit_conf:
            print(f"Avg confidence on top-3 hits: {sum(hit_conf)/len(hit_conf):.3f}")
        if miss_conf:
            print(f"Avg confidence on misses: {sum(miss_conf)/len(miss_conf):.3f}")
    print()
    print("Per-case:")
    for idx, item in enumerate(results, start=1):
        marker = "PASS" if item.top3_hit else "FAIL" if item.has_expectation else "INFO"
        print(
            f"{idx}) {marker} | q='{item.query}' | "
            f"top1={item.top1_hit} top3={item.top3_hit} "
            f"conf={item.confidence:.3f} gap={item.confidence_gap:.3f} "
            f"intent={item.intent_label} low_conf={item.low_confidence}"
        )
        if item.top_titles:
            print(f"   top titles: {item.top_titles}")
        else:
            print("   top titles: []")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run offline retrieval + confidence evaluation cases."
    )
    parser.add_argument(
        "--cases-file",
        required=True,
        help="Path to JSON file containing evaluation cases.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Search result limit per evaluation case.",
    )
    parser.add_argument(
        "--space-key",
        help="Optional fixed space key to apply for all cases.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        cases = _load_cases(args.cases_file)
        agent = _agent_from_env()
    except (ValueError, ConfluenceSearchError) as exc:
        print(f"Error: {exc}")
        return 1

    eval_results: list[EvalCaseResult] = []
    for case in cases:
        query = str(case.get("query", "")).strip()
        if not query:
            continue
        expected_any = _coerce_expected_any(case)
        case_limit = _coerce_limit(case, args.limit)
        case_space_key = _coerce_space_key(case, args.space_key)
        try:
            result = evaluate_case(
                agent,
                query=query,
                expected_any=expected_any,
                limit=case_limit,
                space_key=case_space_key,
            )
        except ConfluenceSearchError as exc:
            print(f"Case failed for '{query}': {exc}")
            continue
        eval_results.append(result)

    _print_summary(eval_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
