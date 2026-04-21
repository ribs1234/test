#!/usr/bin/env python3
"""Lightweight Confluence search agent."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

COMMON_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "he",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
    "this",
    "these",
    "those",
    "or",
    "if",
    "we",
    "you",
    "your",
    "our",
}

INTENT_PREFIX_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (
        "ownership",
        (
            "who owns ",
            "who is responsible for ",
            "owner of ",
            "ownership of ",
            "who manages ",
        ),
        ("owner", "ownership", "oncall", "contact", "team", "service"),
    ),
    (
        "how_to",
        (
            "how do i ",
            "how can i ",
            "how to ",
            "steps to ",
            "process to ",
            "procedure for ",
        ),
        ("runbook", "guide", "steps", "procedure", "playbook", "troubleshooting"),
    ),
    (
        "location",
        (
            "where is ",
            "where can i find ",
            "find me ",
            "find ",
            "looking for ",
            "search for ",
            "show me ",
        ),
        ("overview", "home", "index", "reference", "documentation"),
    ),
    (
        "definition",
        (
            "what is ",
            "what are ",
            "explain ",
            "tell me about ",
        ),
        ("overview", "introduction", "background", "architecture", "reference"),
    ),
]


class ConfluenceSearchError(RuntimeError):
    """Raised when Confluence search fails."""


@dataclass
class SearchResult:
    title: str
    url: str
    summary: str
    space_key: str | None
    last_modified: str | None


@dataclass
class QueryIntent:
    original: str
    search_phrase: str
    keywords: list[str]
    intent_label: str
    boost_terms: list[str]


class ConfluenceSearchAgent:
    """Minimal API client that searches Confluence pages by CQL."""

    def __init__(
        self,
        base_url: str,
        email: str | None = None,
        api_token: str | None = None,
        personal_access_token: str | None = None,
        bearer_token: str | None = None,
        timeout: int = 20,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.api_roots = self._candidate_api_roots(base_url)
        self.email = email
        self.api_token = api_token
        self.personal_access_token = personal_access_token
        self.bearer_token = bearer_token
        self.timeout = timeout

        has_bearer_auth = bool(personal_access_token or bearer_token)
        if not has_bearer_auth and not (email and api_token):
            raise ValueError(
                "Authentication required: set personal access token, bearer token, or email + API token."
            )

    @staticmethod
    def _candidate_api_roots(base_url: str) -> list[str]:
        parsed = urlparse(base_url.strip())
        if not parsed.scheme or not parsed.netloc:
            trimmed = base_url.rstrip("/")
            fallback = [f"{trimmed}/wiki", trimmed]
            return [candidate for candidate in fallback if candidate]

        origin = f"{parsed.scheme}://{parsed.netloc}"
        segments = [segment for segment in parsed.path.split("/") if segment]
        lowered_segments = [segment.lower() for segment in segments]
        known_page_markers = {
            "spaces",
            "display",
            "pages",
            "x",
            "rest",
            "plugins",
            "login.action",
            "dologin.action",
        }

        context_segments: list[str] = []
        if segments:
            if lowered_segments[0] == "wiki":
                context_segments = segments[:1]
            else:
                marker_index = next(
                    (
                        idx
                        for idx, segment in enumerate(lowered_segments)
                        if segment in known_page_markers
                    ),
                    None,
                )
                if marker_index is None:
                    context_segments = segments
                else:
                    context_segments = segments[:marker_index]

        context_path = f"/{'/'.join(context_segments)}" if context_segments else ""
        candidates: list[str] = []

        def add_candidate(path: str) -> None:
            candidate = f"{origin}{path}".rstrip("/")
            if not candidate:
                return
            if candidate not in candidates:
                candidates.append(candidate)

        if context_path:
            add_candidate(context_path)
            if context_path.endswith("/wiki"):
                without_wiki = context_path[: -len("/wiki")]
                add_candidate(without_wiki)
            else:
                add_candidate(f"{context_path}/wiki")
        else:
            add_candidate("/wiki")

        add_candidate("")
        return candidates

    @staticmethod
    def _strip_html(text: str) -> str:
        without_tags = re.sub(r"<[^>]+>", "", text or "")
        clean = re.sub(r"\s+", " ", unescape(without_tags)).strip()
        return clean

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        bearer = self.personal_access_token or self.bearer_token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
            return headers

        raw = f"{self.email}:{self.api_token}".encode("utf-8")
        token = base64.b64encode(raw).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
        return headers

    @staticmethod
    def _decode_body(payload: bytes, content_type: str) -> str:
        match = re.search(r"charset=([^\s;]+)", content_type or "", flags=re.IGNORECASE)
        encoding = (match.group(1).strip('"\'') if match else "") or "utf-8"
        try:
            return payload.decode(encoding, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")

    @staticmethod
    def _one_line_snippet(text: str, *, max_len: int = 220) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if len(compact) <= max_len:
            return compact
        return f"{compact[:max_len]}..."

    @staticmethod
    def _escape_cql_text(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _query_intent(query: str) -> QueryIntent:
        original = re.sub(r"\s+", " ", query or "").strip()
        if not original:
            return QueryIntent(
                original="",
                search_phrase="",
                keywords=[],
                intent_label="general",
                boost_terms=[],
            )

        lower = original.lower()
        intent_label = "general"
        boost_terms: list[str] = []
        for label, prefixes, intent_terms in INTENT_PREFIX_RULES:
            matched = next((prefix for prefix in prefixes if lower.startswith(prefix)), None)
            if matched:
                intent_label = label
                boost_terms = list(intent_terms)
                original = original[len(matched) :].strip()
                lower = original.lower()
                break

        search_phrase = original.rstrip(" ?.!")
        token_source = re.sub(r"[^a-zA-Z0-9\s\-_/]", " ", search_phrase.lower())
        raw_tokens = [token for token in token_source.split() if token]
        stop_words = {
            "a",
            "an",
            "the",
            "and",
            "or",
            "for",
            "to",
            "of",
            "in",
            "on",
            "at",
            "by",
            "with",
            "from",
            "about",
            "into",
            "is",
            "are",
            "be",
            "this",
            "that",
            "these",
            "those",
            "i",
            "me",
            "my",
            "we",
            "our",
            "us",
            "you",
            "your",
            "it",
            "its",
            "can",
            "do",
            "does",
            "did",
            "please",
            "page",
            "pages",
            "article",
            "articles",
            "doc",
            "docs",
            "documentation",
            "confluence",
        }

        keywords: list[str] = []
        for token in raw_tokens:
            if len(token) < 3:
                continue
            if token in stop_words:
                continue
            if token not in keywords:
                keywords.append(token)

        return QueryIntent(
            original=query.strip(),
            search_phrase=search_phrase,
            keywords=keywords[:8],
            intent_label=intent_label,
            boost_terms=boost_terms,
        )

    @staticmethod
    def _summarize_text(text: str, *, max_len: int = 320) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if not compact:
            return ""
        if len(compact) <= max_len:
            return compact
        clipped = compact[:max_len].rstrip()
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        return f"{clipped}..."

    @staticmethod
    def _sentence_split(text: str) -> list[str]:
        return [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", text)
            if sentence and sentence.strip()
        ]

    @staticmethod
    def _tokenize_words(text: str) -> list[str]:
        return [word.lower() for word in re.findall(r"[A-Za-z0-9']+", text)]

    def _ai_generate_summary(self, text: str, *, query: str) -> str:
        clean = re.sub(r"\s+", " ", text or "").strip()
        if not clean:
            return ""

        sentences = self._sentence_split(clean)
        if not sentences:
            return self._summarize_text(clean)

        query_terms = {
            token
            for token in self._tokenize_words(query)
            if token not in COMMON_STOPWORDS and len(token) > 1
        }
        doc_words = [
            token
            for token in self._tokenize_words(clean)
            if token not in COMMON_STOPWORDS and len(token) > 1
        ]
        word_freq: dict[str, int] = {}
        for token in doc_words:
            word_freq[token] = word_freq.get(token, 0) + 1

        scored: list[tuple[int, float, str]] = []
        for idx, sentence in enumerate(sentences):
            sentence_words = [
                token
                for token in self._tokenize_words(sentence)
                if token not in COMMON_STOPWORDS and len(token) > 1
            ]
            if not sentence_words:
                continue
            frequency_score = sum(word_freq.get(token, 0) for token in sentence_words)
            query_boost = sum(2.0 for token in sentence_words if token in query_terms)
            normalized_score = (frequency_score + query_boost) / max(
                len(sentence_words), 1
            )
            scored.append((idx, normalized_score, sentence.strip()))

        if not scored:
            return self._summarize_text(sentences[0])

        top_count = 2 if len(scored) > 1 else 1
        top_ranked = sorted(scored, key=lambda item: item[1], reverse=True)[:top_count]
        ordered = [item[2] for item in sorted(top_ranked, key=lambda item: item[0])]
        summary = " ".join(ordered)
        return self._summarize_text(summary)

    def _body_text_from_search_entry(self, entry: dict[str, Any]) -> str:
        content = entry.get("content", {})
        body = content.get("body", {})
        for body_key in ("view", "storage", "export_view"):
            html_value = (body.get(body_key) or {}).get("value", "")
            text = self._strip_html(html_value)
            if text:
                return text
        return ""

    def _fetch_page_summary(self, api_root: str, page_id: str, *, query: str) -> str | None:
        params = urlencode({"expand": "body.view"})
        url = f"{api_root}/rest/api/content/{page_id}?{params}"
        req = Request(url=url, headers=self._headers(), method="GET")
        try:
            with urlopen(req, timeout=self.timeout) as response:
                payload = response.read()
                content_type = response.headers.get("Content-Type", "")
                data = self._parse_json_payload(
                    payload,
                    content_type=content_type,
                    context=f"Confluence page {page_id} response",
                )
        except (HTTPError, URLError, ConfluenceSearchError):
            return None

        body = data.get("body", {})
        html_value = (body.get("view") or {}).get("value", "")
        text = self._strip_html(html_value)
        if not text:
            return None
        return self._ai_generate_summary(text, query=query)

    def _parse_json_payload(
        self,
        payload: bytes,
        *,
        content_type: str,
        context: str,
    ) -> dict[str, Any]:
        text = self._decode_body(payload, content_type).lstrip("\ufeff").strip()
        lower_text = text[:200].lower()
        if "text/html" in (content_type or "").lower() or lower_text.startswith("<html") or lower_text.startswith("<!doctype html"):
            snippet = self._one_line_snippet(text)
            raise ConfluenceSearchError(
                f"{context} returned HTML instead of JSON (content-type: {content_type or 'unknown'}). "
                f"Response snippet: {snippet or '<empty>'}. "
                "This usually means authentication/SSO intercepted the API call."
            )
        if text.startswith(")]}',"):
            text = text[5:].lstrip()
        elif text.startswith(")]}'"):
            text = text[4:].lstrip()

        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            snippet = self._one_line_snippet(text)
            raise ConfluenceSearchError(
                f"{context} was not valid JSON (content-type: {content_type or 'unknown'}). "
                f"Response snippet: {snippet or '<empty>'}"
            ) from exc

        if not isinstance(data, dict):
            raise ConfluenceSearchError(
                f"{context} returned unexpected JSON type: {type(data).__name__}"
            )
        return data

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        space_key: str | None = None,
    ) -> list[SearchResult]:
        intent = self._query_intent(query)
        if not intent.search_phrase:
            return []

        escaped_phrase = self._escape_cql_text(intent.search_phrase)
        title_or_text_terms = [
            f'title~"{escaped_phrase}"',
            f'text~"{escaped_phrase}"',
        ]
        for keyword in intent.keywords:
            escaped_keyword = self._escape_cql_text(keyword)
            title_or_text_terms.append(f'title~"{escaped_keyword}"')
            title_or_text_terms.append(f'text~"{escaped_keyword}"')

        cql_parts = ["type=page", f"({' or '.join(title_or_text_terms)})"]
        if space_key:
            cql_parts.append(f'space="{space_key}"')
        cql = " and ".join(cql_parts)

        params = urlencode(
            {
                "cql": cql,
                "limit": str(limit),
                "expand": "content.space,content.version,content.body.view",
            }
        )
        attempt_errors: list[str] = []
        for api_root in self.api_roots:
            url = f"{api_root}/rest/api/search?{params}"
            req = Request(url=url, headers=self._headers(), method="GET")

            try:
                with urlopen(req, timeout=self.timeout) as response:
                    payload = response.read()
                    content_type = response.headers.get("Content-Type", "")
                    data = self._parse_json_payload(
                        payload,
                        content_type=content_type,
                        context="Confluence search response",
                    )
                if not isinstance(data.get("results"), list):
                    preview = self._one_line_snippet(json.dumps(data))
                    raise ConfluenceSearchError(
                        f"Confluence search response JSON did not include a 'results' list. "
                        f"Payload snippet: {preview or '<empty>'}"
                    )
                return self._parse_results(
                    data,
                    api_root=api_root,
                    query=query,
                    intent=intent,
                )
            except HTTPError as exc:
                detail = self._extract_error_detail(exc)
                attempt_errors.append(
                    f"{url} -> HTTP {exc.code}: {detail}"
                )
            except URLError as exc:
                attempt_errors.append(f"{url} -> network error: {exc}")
            except ConfluenceSearchError as exc:
                attempt_errors.append(f"{url} -> {exc}")

        joined_errors = " | ".join(attempt_errors)
        raise ConfluenceSearchError(
            "All Confluence API path attempts failed. "
            f"Tried: {', '.join(self.api_roots)}. "
            f"Details: {joined_errors}. "
            "Verify base URL and PAT scope/permissions."
        )

    def _extract_error_detail(self, error: HTTPError) -> str:
        raw = error.read()
        content_type = error.headers.get("Content-Type", "") if error.headers else ""
        try:
            parsed = self._parse_json_payload(
                raw,
                content_type=content_type,
                context="Confluence error response",
            )
            for key in ("message", "reason", "error", "statusMessage"):
                if key in parsed and parsed[key]:
                    return str(parsed[key])
            return self._one_line_snippet(json.dumps(parsed)) or "No error detail provided."
        except ConfluenceSearchError as exc:
            return str(exc)
        except Exception:
            text = self._decode_body(raw, content_type)
            snippet = self._one_line_snippet(text)
            if snippet:
                return (
                    f"Non-JSON error response (content-type: {content_type or 'unknown'}): "
                    f"{snippet}"
                )
            return "No error detail provided."

    def _result_boost_score(self, result: SearchResult, intent: QueryIntent) -> float:
        if intent.intent_label == "general" and not intent.keywords:
            return 0.0

        title = (result.title or "").lower()
        summary = (result.summary or "").lower()
        haystack = f"{title} {summary}"
        score = 0.0

        for keyword in intent.keywords:
            if keyword in title:
                score += 2.6
            elif keyword in haystack:
                score += 1.4

        for term in intent.boost_terms:
            if term in title:
                score += 2.8
            elif term in haystack:
                score += 1.8

        if intent.intent_label == "ownership" and any(
            marker in haystack
            for marker in ("owner", "owned by", "oncall", "contact", "responsible")
        ):
            score += 2.5
        elif intent.intent_label == "how_to" and any(
            marker in haystack
            for marker in ("runbook", "procedure", "steps", "guide", "troubleshooting")
        ):
            score += 2.2
        elif intent.intent_label == "location" and any(
            marker in haystack
            for marker in ("overview", "home", "index", "reference", "catalog")
        ):
            score += 1.8
        elif intent.intent_label == "definition" and any(
            marker in haystack
            for marker in ("overview", "introduction", "architecture", "background")
        ):
            score += 1.7

        return score

    def _rank_results_by_intent(
        self, results: list[SearchResult], intent: QueryIntent
    ) -> list[SearchResult]:
        if len(results) <= 1:
            return results

        scored = [
            (idx, self._result_boost_score(item, intent), item)
            for idx, item in enumerate(results)
        ]
        if not any(score > 0 for _, score, _ in scored):
            return results

        ordered = sorted(scored, key=lambda item: (-item[1], item[0]))
        return [item for _, _, item in ordered]

    def _parse_results(
        self, data: dict[str, Any], *, api_root: str, query: str, intent: QueryIntent
    ) -> list[SearchResult]:
        items: list[SearchResult] = []
        top_links = data.get("_links", {})
        top_base = top_links.get("base", self.base_url)
        summary_cache: dict[str, str] = {}

        for entry in data.get("results", []):
            content = entry.get("content", {})
            links = content.get("_links", {})
            base = links.get("base", top_base)
            webui = links.get("webui")
            page_url = urljoin(f"{base.rstrip('/')}/", webui.lstrip("/")) if webui else ""
            summary = self._ai_generate_summary(
                self._body_text_from_search_entry(entry),
                query=query,
            )
            if not summary:
                page_id = str(content.get("id") or "").strip()
                if page_id:
                    if page_id not in summary_cache:
                        summary_cache[page_id] = (
                            self._fetch_page_summary(api_root, page_id, query=query)
                            or ""
                        )
                    summary = summary_cache[page_id]

            items.append(
                SearchResult(
                    title=content.get("title") or entry.get("title") or "Untitled",
                    url=page_url,
                    summary=summary
                    or "No readable page content available for AI summary.",
                    space_key=(content.get("space") or {}).get("key"),
                    last_modified=(content.get("version") or {}).get("when"),
                )
            )

        return self._rank_results_by_intent(items, intent)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Confluence for pages.")
    parser.add_argument("query", help="Search query text")
    parser.add_argument("--limit", type=int, default=10, help="Max results to return")
    parser.add_argument("--space-key", help="Filter to a Confluence space key")
    parser.add_argument(
        "--base-url",
        default=os.getenv("CONFLUENCE_BASE_URL", ""),
        help="Confluence base URL (or set CONFLUENCE_BASE_URL)",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("CONFLUENCE_EMAIL"),
        help="Atlassian account email (or set CONFLUENCE_EMAIL)",
    )
    parser.add_argument(
        "--api-token",
        default=os.getenv("CONFLUENCE_API_TOKEN"),
        help="Confluence API token (or set CONFLUENCE_API_TOKEN)",
    )
    parser.add_argument(
        "--personal-access-token",
        default=(
            os.getenv("CONFLUENCE_PERSONAL_ACCESS_TOKEN")
            or os.getenv("CONFLUENCE_PAT")
        ),
        help=(
            "Confluence personal access token "
            "(or set CONFLUENCE_PERSONAL_ACCESS_TOKEN / CONFLUENCE_PAT)"
        ),
    )
    parser.add_argument(
        "--bearer-token",
        default=os.getenv("CONFLUENCE_BEARER_TOKEN"),
        help="Bearer token auth (or set CONFLUENCE_BEARER_TOKEN)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("CONFLUENCE_TIMEOUT", "20")),
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    return parser.parse_args(argv)


def print_results(results: list[SearchResult], as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(item) for item in results], indent=2))
        return

    if not results:
        print("No matching pages found.")
        return

    for idx, item in enumerate(results, start=1):
        print(f"{idx}. {item.title}")
        if item.url:
            print(f"   URL: {item.url}")
        if item.space_key:
            print(f"   Space: {item.space_key}")
        if item.last_modified:
            print(f"   Last Modified: {item.last_modified}")
        if item.summary:
            print(f"   Summary: {item.summary}")
        print()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        agent = ConfluenceSearchAgent(
            base_url=args.base_url,
            email=args.email,
            api_token=args.api_token,
            personal_access_token=args.personal_access_token,
            bearer_token=args.bearer_token,
            timeout=args.timeout,
        )
        results = agent.search(args.query, limit=args.limit, space_key=args.space_key)
    except (ValueError, ConfluenceSearchError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_results(results, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
