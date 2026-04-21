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
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


class ConfluenceSearchError(RuntimeError):
    """Raised when Confluence search fails."""


@dataclass
class SearchResult:
    title: str
    url: str
    excerpt: str
    space_key: str | None
    last_modified: str | None


class ConfluenceSearchAgent:
    """Minimal API client that searches Confluence pages by CQL."""

    def __init__(
        self,
        base_url: str,
        email: str | None = None,
        api_token: str | None = None,
        bearer_token: str | None = None,
        timeout: int = 20,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.api_root = self._normalize_api_root(base_url)
        self.email = email
        self.api_token = api_token
        self.bearer_token = bearer_token
        self.timeout = timeout

        if not bearer_token and not (email and api_token):
            raise ValueError(
                "Authentication required: set bearer token or email + API token."
            )

    @staticmethod
    def _normalize_api_root(base_url: str) -> str:
        trimmed = base_url.rstrip("/")
        if trimmed.endswith("/wiki"):
            return trimmed
        return f"{trimmed}/wiki"

    @staticmethod
    def _strip_html(text: str) -> str:
        without_tags = re.sub(r"<[^>]+>", "", text or "")
        clean = re.sub(r"\s+", " ", unescape(without_tags)).strip()
        return clean

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
            return headers

        raw = f"{self.email}:{self.api_token}".encode("utf-8")
        token = base64.b64encode(raw).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
        return headers

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        space_key: str | None = None,
    ) -> list[SearchResult]:
        if not query.strip():
            return []

        escaped_query = query.replace('"', '\\"')
        cql_parts = ['type=page', f'text~"{escaped_query}"']
        if space_key:
            cql_parts.append(f'space="{space_key}"')
        cql = " and ".join(cql_parts)

        params = urlencode(
            {
                "cql": cql,
                "limit": str(limit),
                "expand": "content.space,content.version",
            }
        )
        url = f"{self.api_root}/rest/api/search?{params}"
        req = Request(url=url, headers=self._headers(), method="GET")

        try:
            with urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = self._extract_error_detail(exc)
            raise ConfluenceSearchError(
                f"Confluence request failed ({exc.code}): {detail}"
            ) from exc
        except URLError as exc:
            raise ConfluenceSearchError(f"Could not connect to Confluence: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfluenceSearchError("Confluence returned invalid JSON.") from exc

        return self._parse_results(data)

    def _extract_error_detail(self, error: HTTPError) -> str:
        try:
            payload = error.read().decode("utf-8")
            parsed = json.loads(payload)
            for key in ("message", "reason", "error", "statusMessage"):
                if key in parsed and parsed[key]:
                    return str(parsed[key])
            return payload or "No error detail provided."
        except Exception:
            return "No error detail provided."

    def _parse_results(self, data: dict[str, Any]) -> list[SearchResult]:
        items: list[SearchResult] = []
        top_links = data.get("_links", {})
        top_base = top_links.get("base", self.base_url)

        for entry in data.get("results", []):
            content = entry.get("content", {})
            links = content.get("_links", {})
            base = links.get("base", top_base)
            webui = links.get("webui")
            page_url = urljoin(f"{base.rstrip('/')}/", webui.lstrip("/")) if webui else ""

            items.append(
                SearchResult(
                    title=content.get("title") or entry.get("title") or "Untitled",
                    url=page_url,
                    excerpt=self._strip_html(entry.get("excerpt", "")),
                    space_key=(content.get("space") or {}).get("key"),
                    last_modified=(content.get("version") or {}).get("when"),
                )
            )

        return items


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
        if item.excerpt:
            print(f"   Excerpt: {item.excerpt}")
        print()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        agent = ConfluenceSearchAgent(
            base_url=args.base_url,
            email=args.email,
            api_token=args.api_token,
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
