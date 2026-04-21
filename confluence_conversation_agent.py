#!/usr/bin/env python3
"""Conversation layer for Confluence search."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from confluence_search_agent import (
    ConfluenceSearchAgent,
    ConfluenceSearchError,
    QueryIntent,
    SearchResult,
)


@dataclass
class ConversationState:
    base_url: str = ""
    personal_access_token: str | None = None
    email: str | None = None
    api_token: str | None = None
    space_key: str | None = None
    limit: int = 10
    last_query: str = ""
    last_results: list[SearchResult] = field(default_factory=list)


@dataclass
class ConversationTurn:
    reply: str
    results: list[SearchResult]


class ConfluenceConversationAgent:
    """Simple multi-turn layer on top of Confluence search."""

    def __init__(self, state: ConversationState) -> None:
        self.state = state

    def handle_message(self, message: str, settings: dict[str, Any]) -> ConversationTurn:
        text = re.sub(r"\s+", " ", str(message or "")).strip()
        if not text:
            return ConversationTurn(
                reply="Ask a question like 'who owns vault oncall' or 'where is the vault runbook?'.",
                results=[],
            )

        self._apply_settings(settings)
        self._apply_inline_filters(text)
        if not self.state.base_url:
            return ConversationTurn(
                reply="Set a Confluence Base URL before starting the conversation.",
                results=[],
            )

        if not (
            self.state.personal_access_token
            or (self.state.email and self.state.api_token)
        ):
            return ConversationTurn(
                reply="Provide a Personal Access Token, or email + API token, to continue.",
                results=[],
            )

        detail_idx = self._requested_result_index(text)
        if detail_idx is not None:
            return self._result_detail_turn(detail_idx)

        if self._is_more_request(text):
            if not self.state.last_query:
                return ConversationTurn(
                    reply="There is no previous search yet. Ask a new question first.",
                    results=[],
                )
            self.state.limit = min(self.state.limit + 5, 50)
            return self._search_turn(self.state.last_query, from_more=True)

        if self._is_repeat_request(text):
            if not self.state.last_query:
                return ConversationTurn(
                    reply="There is no previous query to repeat yet.",
                    results=[],
                )
            return self._search_turn(self.state.last_query, from_more=False)

        return self._search_turn(text, from_more=False)

    def _apply_settings(self, settings: dict[str, Any]) -> None:
        base_url = str(settings.get("base_url", "")).strip()
        if base_url:
            self.state.base_url = base_url

        pat = str(settings.get("personal_access_token", "")).strip()
        if pat:
            self.state.personal_access_token = pat

        email = str(settings.get("email", "")).strip()
        if email:
            self.state.email = email

        api_token = str(settings.get("api_token", "")).strip()
        if api_token:
            self.state.api_token = api_token

        if "space_key" in settings:
            raw_space = settings.get("space_key")
            if raw_space is None:
                self.state.space_key = None
            else:
                space = str(raw_space).strip().upper()
                self.state.space_key = space or None

        if "limit" in settings:
            try:
                parsed = int(settings.get("limit", self.state.limit))
            except (TypeError, ValueError):
                parsed = self.state.limit
            self.state.limit = max(1, min(parsed, 50))

    def _apply_inline_filters(self, text: str) -> None:
        lower = text.lower()
        if any(phrase in lower for phrase in ("all spaces", "any space", "clear space filter")):
            self.state.space_key = None
            return

        in_space = re.search(r"\bin\s+([A-Za-z][A-Za-z0-9_]{1,15})\s+space\b", text, re.IGNORECASE)
        if in_space:
            self.state.space_key = in_space.group(1).upper()
            return

        explicit_space = re.search(r"\bspace\s+([A-Za-z][A-Za-z0-9_]{1,15})\b", text, re.IGNORECASE)
        if explicit_space:
            self.state.space_key = explicit_space.group(1).upper()

    @staticmethod
    def _is_more_request(text: str) -> bool:
        lower = text.strip().lower()
        return lower in {
            "more",
            "show more",
            "more results",
            "next",
            "next results",
            "show me more",
        }

    @staticmethod
    def _is_repeat_request(text: str) -> bool:
        lower = text.strip().lower()
        return lower in {"again", "rerun", "run again", "repeat", "refresh"}

    @staticmethod
    def _requested_result_index(text: str) -> int | None:
        if not re.search(r"\b(result|#)\b", text, re.IGNORECASE):
            return None
        if not re.search(
            r"\b(summary|summarize|detail|details|explain|about|open)\b",
            text,
            re.IGNORECASE,
        ):
            return None
        match = re.search(r"(?:result|#)\s*(\d{1,2})", text, re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1))

    def _search_turn(self, query: str, *, from_more: bool) -> ConversationTurn:
        agent = ConfluenceSearchAgent(
            base_url=self.state.base_url,
            personal_access_token=self.state.personal_access_token,
            email=self.state.email,
            api_token=self.state.api_token,
        )
        results = agent.search(query=query, limit=self.state.limit, space_key=self.state.space_key)
        self.state.last_query = query
        self.state.last_results = results

        intent = ConfluenceSearchAgent._query_intent(query)
        scope = f" in space {self.state.space_key}" if self.state.space_key else ""
        if not results:
            return ConversationTurn(
                reply=(
                    f"I couldn't find matching pages{scope} for '{query}'. "
                    "Try refining the question, changing the space filter, or asking for more general terms."
                ),
                results=[],
            )

        top_titles = ", ".join(item.title for item in results[:3])
        mode = "I fetched additional matches. " if from_more else ""
        return ConversationTurn(
            reply=(
                f"{mode}I found {len(results)} page(s){scope} for '{query}'. "
                f"Detected intent: {self._intent_label(intent)}. "
                f"Top matches: {top_titles}. "
                "You can ask 'summarize result 2' or 'show more'."
            ),
            results=results,
        )

    def _result_detail_turn(self, index: int) -> ConversationTurn:
        if not self.state.last_results:
            return ConversationTurn(
                reply="No prior results are available yet. Ask a search question first.",
                results=[],
            )
        if index < 1 or index > len(self.state.last_results):
            return ConversationTurn(
                reply=f"Result {index} is out of range. Available range is 1-{len(self.state.last_results)}.",
                results=self.state.last_results,
            )

        item = self.state.last_results[index - 1]
        detail = [
            f"Result {index}: {item.title}.",
            f"Summary: {item.summary}",
        ]
        if item.space_key:
            detail.append(f"Space: {item.space_key}.")
        if item.last_modified:
            detail.append(f"Last modified: {item.last_modified}.")
        if item.url:
            detail.append(f"Link: {item.url}")
        return ConversationTurn(reply=" ".join(detail), results=self.state.last_results)

    @staticmethod
    def _intent_label(intent: QueryIntent) -> str:
        mapping = {
            "ownership": "ownership / responsibility",
            "how_to": "how-to / procedure",
            "location": "location / find-doc",
            "definition": "definition / overview",
            "general": "general",
        }
        return mapping.get(intent.intent_label, intent.intent_label)


def serialize_results(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [
        {
            "title": item.title,
            "url": item.url,
            "summary": item.summary,
            "space_key": item.space_key,
            "last_modified": item.last_modified,
        }
        for item in results
    ]
