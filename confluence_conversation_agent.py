#!/usr/bin/env python3
"""Conversation layer for Confluence search."""

from __future__ import annotations

import re
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Any

from confluence_search_agent import (
    ConfluenceSearchAgent,
    ConfluenceSearchError,
    QueryIntent,
    SearchResult,
)

BOT_NAME = "Einstein"
DEFAULT_LIMIT = 10


@dataclass
class ConversationState:
    base_url: str = ""
    personal_access_token: str | None = None
    email: str | None = None
    api_token: str | None = None
    space_key: str | None = None
    space_keys: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    modified_since: str | None = None
    modified_before: str | None = None
    limit: int = DEFAULT_LIMIT
    last_query: str = ""
    last_results: list[SearchResult] = field(default_factory=list)
    last_page_ids: list[str | None] = field(default_factory=list)
    previous_query: str = ""
    previous_results: list[SearchResult] = field(default_factory=list)
    previous_page_ids: list[str | None] = field(default_factory=list)
    pending_clarification_query: str = ""
    pending_clarification_options: list[str] = field(default_factory=list)


@dataclass
class ConversationTurn:
    reply: str
    results: list[SearchResult]
    page_ids: list[str | None] = field(default_factory=list)


class ConfluenceConversationAgent:
    """Simple multi-turn layer on top of Confluence search."""

    def __init__(self, state: ConversationState) -> None:
        self.state = state

    def _speak(self, message: str) -> str:
        return f"{BOT_NAME}: {message}"

    def handle_message(self, message: str, settings: dict[str, Any]) -> ConversationTurn:
        text = re.sub(r"\s+", " ", str(message or "")).strip()
        if not text:
            return ConversationTurn(
                reply=self._speak(
                    "Ask a question like 'who owns vault oncall' or "
                    "'where is the vault runbook?'."
                ),
                results=[],
                page_ids=[],
            )

        self._apply_settings(settings)
        self._apply_inline_filters(text)
        if self.state.pending_clarification_query and self._is_clarification_response(text):
            refined_query = self._resolve_clarification_query(text)
            if not refined_query:
                option_count = len(self.state.pending_clarification_options)
                return ConversationTurn(
                    reply=self._speak(
                        "I still need a refinement to continue. "
                        f"Reply with a number (1-{option_count}) or add more detail."
                    ),
                    results=self.state.last_results,
                    page_ids=self.state.last_page_ids,
                )
            self._clear_pending_clarification()
            return self._search_turn(
                refined_query,
                from_more=False,
                allow_clarification=False,
            )
        detail_idx = self._requested_result_index(text)
        if detail_idx is not None:
            return self._result_detail_turn(detail_idx)

        compare_indices = self._requested_compare_indices(text)
        if compare_indices is not None:
            return self._compare_results_turn(compare_indices[0], compare_indices[1])
        changed_idx = self._requested_changed_index(text)
        if changed_idx is not None:
            return self._result_changes_turn(changed_idx)

        if not self.state.base_url:
            return ConversationTurn(
                reply=self._speak(
                    "Set a Confluence Base URL before starting the conversation."
                ),
                results=[],
                page_ids=[],
            )

        if not (
            self.state.personal_access_token
            or (self.state.email and self.state.api_token)
        ):
            return ConversationTurn(
                reply=self._speak(
                    "Provide a Personal Access Token, or email + API token, to continue."
                ),
                results=[],
                page_ids=[],
            )

        if self._is_more_request(text):
            if not self.state.last_query:
                return ConversationTurn(
                    reply=self._speak(
                        "There is no previous search yet. Ask a new question first."
                    ),
                    results=[],
                    page_ids=[],
                )
            self.state.limit = min(self.state.limit + 5, 50)
            return self._search_turn(
                self.state.last_query,
                from_more=True,
                allow_clarification=False,
            )

        if self._is_repeat_request(text):
            if not self.state.last_query:
                return ConversationTurn(
                    reply=self._speak("There is no previous query to repeat yet."),
                    results=[],
                    page_ids=[],
                )
            return self._search_turn(
                self.state.last_query,
                from_more=False,
                allow_clarification=False,
            )

        search_query = self._search_query_from_message(text)
        return self._search_turn(search_query, from_more=False, allow_clarification=True)

    @staticmethod
    def _is_clarification_response(text: str) -> bool:
        lower = text.strip().lower()
        if lower in {
            "more",
            "show more",
            "again",
            "repeat",
            "rerun",
            "refresh",
        }:
            return False
        if re.search(
            r"\b(result|#)\s*\d{1,2}\b", lower
        ) and re.search(r"\b(summary|summarize|detail|compare|changed|new)\b", lower):
            return False
        return True

    @staticmethod
    def _ordinal_to_index(text: str) -> int | None:
        lower = text.lower()
        mapping = {
            "first": 1,
            "second": 2,
            "third": 3,
            "fourth": 4,
            "fifth": 5,
        }
        for key, value in mapping.items():
            if re.search(rf"\b{key}\b", lower):
                return value
        return None

    def _resolve_clarification_query(self, text: str) -> str | None:
        base_query = self.state.pending_clarification_query.strip()
        if not base_query:
            return None
        options = self.state.pending_clarification_options
        if options:
            numeric = re.search(r"^(?:option|result|#)?\s*(\d{1,2})$", text.strip(), re.IGNORECASE)
            if numeric:
                idx = int(numeric.group(1))
                if 1 <= idx <= len(options):
                    return f"{base_query} {options[idx - 1]}"
                return None
            ordinal = self._ordinal_to_index(text)
            if ordinal and 1 <= ordinal <= len(options):
                return f"{base_query} {options[ordinal - 1]}"

        refinement = self._search_query_from_message(text).strip()
        if not refinement:
            return None
        if refinement.lower() in {"same", "either", "any"}:
            return base_query
        return f"{base_query} {refinement}"

    def _clear_pending_clarification(self) -> None:
        self.state.pending_clarification_query = ""
        self.state.pending_clarification_options = []

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
                self.state.space_keys = []
            else:
                space = str(raw_space).strip().upper()
                self.state.space_key = space or None
                self.state.space_keys = [space] if space else []

        if "space_keys" in settings:
            incoming = settings.get("space_keys")
            if isinstance(incoming, list):
                normalized: list[str] = []
                for item in incoming:
                    value = re.sub(r"\s+", "", str(item or "")).upper()
                    if not value or value in normalized:
                        continue
                    normalized.append(value)
                self.state.space_keys = normalized
                self.state.space_key = normalized[0] if normalized else None

        if "exclude_terms" in settings:
            incoming_exclusions = settings.get("exclude_terms")
            if isinstance(incoming_exclusions, list):
                normalized_terms: list[str] = []
                seen_terms: set[str] = set()
                for item in incoming_exclusions:
                    text = re.sub(r"\s+", " ", str(item or "")).strip()
                    if not text:
                        continue
                    key = text.lower()
                    if key in seen_terms:
                        continue
                    seen_terms.add(key)
                    normalized_terms.append(text)
                self.state.exclude_terms = normalized_terms

        if "modified_since" in settings:
            candidate = str(settings.get("modified_since") or "").strip()
            self.state.modified_since = (
                candidate if self._is_valid_iso_date(candidate) else None
            )
        if "modified_before" in settings:
            candidate = str(settings.get("modified_before") or "").strip()
            self.state.modified_before = (
                candidate if self._is_valid_iso_date(candidate) else None
            )

        if "limit" in settings:
            try:
                parsed = int(settings.get("limit", self.state.limit))
            except (TypeError, ValueError):
                parsed = self.state.limit
            self.state.limit = max(1, min(parsed, 50))

    def _apply_inline_filters(self, text: str) -> None:
        lower = text.lower()
        if any(
            phrase in lower
            for phrase in (
                "clear all filters",
                "reset all filters",
                "remove all filters",
            )
        ):
            self.state.space_key = None
            self.state.space_keys = []
            self.state.exclude_terms = []
            self.state.modified_since = None
            self.state.modified_before = None

        if any(phrase in lower for phrase in ("all spaces", "any space", "clear space filter")):
            self.state.space_key = None
            self.state.space_keys = []

        extracted_space_keys = self._extract_space_keys(text)
        if extracted_space_keys:
            self.state.space_keys = extracted_space_keys
            self.state.space_key = extracted_space_keys[0]

        if any(
            phrase in lower
            for phrase in (
                "clear exclude filter",
                "clear exclusion filter",
                "clear excludes",
                "include all terms",
            )
        ):
            self.state.exclude_terms = []
        extracted_exclusions = self._extract_exclude_terms(text)
        if extracted_exclusions:
            self.state.exclude_terms = extracted_exclusions

        if any(
            phrase in lower
            for phrase in ("clear date filter", "any date", "remove date filter")
        ):
            self.state.modified_since = None
            self.state.modified_before = None
        inferred_dates = self._extract_date_filters(text)
        if inferred_dates is not None:
            self.state.modified_since, self.state.modified_before = inferred_dates

        # Let Einstein infer result count directly from the user's question.
        if any(
            phrase in lower
            for phrase in ("default result count", "reset result count", "default limit")
        ):
            self.state.limit = DEFAULT_LIMIT
        else:
            inferred_limit = self._infer_limit_from_text(text)
            if inferred_limit is not None:
                self.state.limit = inferred_limit

    @staticmethod
    def _is_valid_iso_date(value: str) -> bool:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
            return False
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _normalize_space_tokens(segment: str) -> list[str]:
        cleaned = re.sub(r"\b(?:and|or|&)\b", ",", segment, flags=re.IGNORECASE)
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]{1,15}", cleaned)
        ignored = {"in", "space", "spaces", "and", "or", "all", "any", "the"}
        normalized: list[str] = []
        for token in tokens:
            upper = token.upper()
            if token.lower() in ignored or upper in normalized:
                continue
            normalized.append(upper)
        return normalized

    def _extract_space_keys(self, text: str) -> list[str]:
        found: list[str] = []
        for match in re.finditer(
            r"\bin\s+([A-Za-z0-9_,\s&]+?)\s+spaces?\b", text, re.IGNORECASE
        ):
            found.extend(self._normalize_space_tokens(match.group(1)))

        if not found:
            single_space = re.search(
                r"\bin\s+([A-Za-z][A-Za-z0-9_]{1,15})\s+space\b",
                text,
                re.IGNORECASE,
            )
            if single_space:
                found.append(single_space.group(1).upper())
            else:
                explicit_space = re.search(
                    r"\bspace\s+([A-Za-z][A-Za-z0-9_]{1,15})\b", text, re.IGNORECASE
                )
                if explicit_space:
                    found.append(explicit_space.group(1).upper())

        deduped: list[str] = []
        for token in found:
            if token not in deduped:
                deduped.append(token)
        return deduped

    @staticmethod
    def _split_exclusion_segment(segment: str) -> list[str]:
        extracted: list[str] = []
        working = segment.strip()
        for quoted in re.findall(r'"([^"]+)"|\'([^\']+)\'', working):
            phrase = (quoted[0] or quoted[1] or "").strip()
            if phrase:
                extracted.append(phrase)
        working = re.sub(r'"[^"]+"|\'[^\']+\'', " ", working)
        normalized = re.sub(
            r"\s*(?:,|;|\band\b|\bor\b)\s*",
            ",",
            working,
            flags=re.IGNORECASE,
        )
        for part in normalized.split(","):
            cleaned = re.sub(r"^[^\w]+|[^\w-]+$", "", part.strip())
            if cleaned and len(cleaned) > 1:
                extracted.append(cleaned)
        deduped: list[str] = []
        seen: set[str] = set()
        for term in extracted:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
        return deduped

    def _extract_exclude_terms(self, text: str) -> list[str]:
        terms: list[str] = []
        for match in re.finditer(
            r"\b(?:exclude|excluding|without)\s+(.+?)(?=(?:\bin\s+[A-Za-z][A-Za-z0-9_]{1,15}\s+space|\bin\s+[A-Za-z0-9_,\s&]+?\s+spaces?\b|\b(?:top|first|show)\s+\d{1,2}\b|\b(?:since|before|after|from|between|last|this|today|yesterday)\b|\b(?:who|what|where|how|why|when)\b|$))",
            text,
            re.IGNORECASE,
        ):
            terms.extend(self._split_exclusion_segment(match.group(1)))
        for minus in re.findall(r"(?:^|\s)-([A-Za-z0-9][A-Za-z0-9_-]{2,})", text):
            terms.append(minus.strip())

        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
        return deduped

    def _extract_date_filters(self, text: str) -> tuple[str | None, str | None] | None:
        lower = text.lower()
        today = date.today()

        between = re.search(
            r"\bbetween\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})\b",
            lower,
        )
        if between:
            first, second = between.group(1), between.group(2)
            if self._is_valid_iso_date(first) and self._is_valid_iso_date(second):
                if first <= second:
                    return first, second
                return second, first

        since_match = re.search(r"\b(?:since|after|from)\s+(\d{4}-\d{2}-\d{2})\b", lower)
        before_match = re.search(r"\b(?:before|until)\s+(\d{4}-\d{2}-\d{2})\b", lower)
        if since_match or before_match:
            since_value = since_match.group(1) if since_match else None
            before_value = before_match.group(1) if before_match else None
            if since_value and not self._is_valid_iso_date(since_value):
                since_value = None
            if before_value and not self._is_valid_iso_date(before_value):
                before_value = None
            return since_value, before_value

        relative = re.search(r"\blast\s+(\d{1,3})\s+(day|days|week|weeks|month|months)\b", lower)
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            if "day" in unit:
                delta = timedelta(days=amount)
            elif "week" in unit:
                delta = timedelta(weeks=amount)
            else:
                delta = timedelta(days=amount * 30)
            since = (today - delta).isoformat()
            return since, today.isoformat()

        if "this week" in lower:
            start = today - timedelta(days=today.weekday())
            return start.isoformat(), today.isoformat()
        if "this month" in lower:
            start = today.replace(day=1)
            return start.isoformat(), today.isoformat()
        if re.search(r"\btoday\b", lower):
            iso = today.isoformat()
            return iso, iso
        if re.search(r"\byesterday\b", lower):
            prior = (today - timedelta(days=1)).isoformat()
            return prior, prior
        return None

    @staticmethod
    def _infer_limit_from_text(text: str) -> int | None:
        lower = text.lower()
        if (
            re.search(r"\bshow all\b", lower)
            or re.search(r"\ball results?\b", lower)
            or re.search(r"\ball pages?\b", lower)
        ):
            return 50

        patterns = (
            r"\btop\s+(\d{1,2})\b",
            r"\bfirst\s+(\d{1,2})\b",
            r"\bshow\s+(\d{1,2})\s+(?:results?|pages?)\b",
            r"\b(\d{1,2})\s+(?:results?|pages?)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, lower)
            if not match:
                continue
            try:
                count = int(match.group(1))
            except (TypeError, ValueError):
                continue
            return max(1, min(count, 50))
        return None

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
    def _search_query_from_message(text: str) -> str:
        cleaned = text
        patterns = (
            r"\bin\s+[A-Za-z0-9_,\s&]+?\s+spaces?\b",
            r"\bin\s+[A-Za-z][A-Za-z0-9_]{1,15}\s+space\b",
            r"\bspace\s+[A-Za-z][A-Za-z0-9_]{1,15}\b",
            r"\b(?:top|first)\s+\d{1,2}\b",
            r"\bshow\s+\d{1,2}\s+(?:results?|pages?)\b",
            r"\b(?:all results?|all pages?|show all)\b",
            r"\b(?:exclude|excluding|without)\s+(.+?)(?=(?:\bin\s+[A-Za-z][A-Za-z0-9_]{1,15}\s+space|\bin\s+[A-Za-z0-9_,\s&]+?\s+spaces?\b|\b(?:top|first|show)\s+\d{1,2}\b|\b(?:since|before|after|from|between|last|this|today|yesterday)\b|\b(?:who|what|where|how|why|when)\b|$))",
            r"\bbetween\s+\d{4}-\d{2}-\d{2}\s+and\s+\d{4}-\d{2}-\d{2}\b",
            r"\b(?:since|before|after|from|until)\s+\d{4}-\d{2}-\d{2}\b",
            r"\blast\s+\d{1,3}\s+(?:day|days|week|weeks|month|months)\b",
            r"\bthis\s+(?:week|month)\b",
            r"\b(?:today|yesterday)\b",
            r"\b(?:clear|reset|remove)\s+(?:all\s+)?filters?\b",
        )
        for pattern in patterns:
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(?:show|show me|find|find me|search for)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
        return cleaned or text

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

    @staticmethod
    def _requested_compare_indices(text: str) -> tuple[int, int] | None:
        if not re.search(r"\bcompare\b", text, re.IGNORECASE):
            return None

        explicit = re.search(
            r"\bcompare\s+(?:result|#)?\s*(\d{1,2})\s*(?:and|vs\.?|versus|with)\s*(?:result|#)?\s*(\d{1,2})\b",
            text,
            re.IGNORECASE,
        )
        if explicit:
            return int(explicit.group(1)), int(explicit.group(2))

        matches = re.findall(r"(?:result|#)\s*(\d{1,2})", text, re.IGNORECASE)
        if len(matches) >= 2:
            return int(matches[0]), int(matches[1])
        return None

    @staticmethod
    def _requested_changed_index(text: str) -> int | None:
        patterns = (
            r"\bwhat(?:'s| is)?\s+(?:changed|new)\s+(?:in\s+)?(?:result|#)\s*(\d{1,2})\b",
            r"\bchanges?\s+(?:for|in)\s+(?:result|#)\s*(\d{1,2})\b",
            r"\b(?:result|#)\s*(\d{1,2})\s+(?:what(?:'s| is)?\s+)?(?:changed|new)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _question_word_tokens(text: str) -> set[str]:
        raw = re.findall(r"[A-Za-z0-9]{3,}", text or "")
        stop = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "into",
            "about",
            "where",
            "what",
            "when",
            "how",
            "who",
            "why",
            "please",
            "show",
            "find",
            "search",
            "page",
            "pages",
            "result",
            "results",
            "confluence",
        }
        return {token.lower() for token in raw if token.lower() not in stop}

    def _result_query_overlap(self, result: SearchResult, query_terms: set[str]) -> float:
        if not query_terms:
            return 0.0
        haystack = f"{result.title} {result.summary}".lower()
        hits = sum(1 for token in query_terms if token in haystack)
        return hits / max(len(query_terms), 1)

    def _should_ask_clarification(
        self, *, query: str, intent: QueryIntent, results: list[SearchResult]
    ) -> bool:
        if len(results) < 2:
            return False
        query_terms = self._question_word_tokens(query)
        if intent.intent_label == "general" and len(query_terms) <= 2:
            return True

        overlaps = [self._result_query_overlap(item, query_terms) for item in results[:3]]
        if not overlaps:
            return False
        top_overlap = max(overlaps)
        if top_overlap < 0.2:
            return True
        if len(overlaps) >= 2 and abs(overlaps[0] - overlaps[1]) < 0.08 and overlaps[0] < 0.5:
            return True
        return False

    def _clarification_options_from_results(
        self, results: list[SearchResult], *, max_options: int = 3
    ) -> list[str]:
        options: list[str] = []
        for item in results:
            title = re.sub(r"\s+", " ", (item.title or "").strip())
            if not title:
                continue
            lowered = title.lower()
            if lowered in {existing.lower() for existing in options}:
                continue
            options.append(title)
            if len(options) >= max_options:
                break
        return options

    def _clarification_turn(
        self, *, query: str, results: list[SearchResult], page_ids: list[str | None]
    ) -> ConversationTurn:
        options = self._clarification_options_from_results(results)
        if not options:
            return ConversationTurn(
                reply=self._speak(
                    "I found multiple possible matches. Can you add one detail "
                    "(team name, space, or date range) so I can narrow this down?"
                ),
                results=results,
                page_ids=page_ids,
            )

        self.state.pending_clarification_query = query
        self.state.pending_clarification_options = options
        numbered = "\n".join(f"{idx}) {option}" for idx, option in enumerate(options, start=1))
        return ConversationTurn(
            reply=self._speak(
                "I found a few plausible interpretations. Which one did you mean?\n"
                f"{numbered}\n"
                "Reply with a number, or add a detail (for example: team, space, or date range)."
            ),
            results=results,
            page_ids=page_ids,
        )

    def _search_turn(
        self, query: str, *, from_more: bool, allow_clarification: bool
    ) -> ConversationTurn:
        prior_results = list(self.state.last_results)
        prior_page_ids = list(self.state.last_page_ids)
        prior_query = self.state.last_query
        agent = ConfluenceSearchAgent(
            base_url=self.state.base_url,
            personal_access_token=self.state.personal_access_token,
            email=self.state.email,
            api_token=self.state.api_token,
        )
        results = agent.search(
            query=query,
            limit=self.state.limit,
            space_key=self.state.space_key,
            space_keys=self.state.space_keys,
            exclude_terms=self.state.exclude_terms,
            modified_since=self.state.modified_since,
            modified_before=self.state.modified_before,
        )
        page_ids = [item.page_id for item in results]
        self.state.previous_query = prior_query
        self.state.previous_results = prior_results
        self.state.previous_page_ids = prior_page_ids
        self.state.last_query = query
        self.state.last_results = results
        self.state.last_page_ids = page_ids

        intent = ConfluenceSearchAgent._query_intent(query)
        filter_scope = self._active_filter_scope()
        if not results:
            self._clear_pending_clarification()
            return ConversationTurn(
                reply=self._speak(
                    f"I couldn't find matching pages{filter_scope} for '{query}'. "
                    "Try refining the question, changing the space filter, or asking for more general terms."
                ),
                results=[],
                page_ids=[],
            )
        if allow_clarification and self._should_ask_clarification(
            query=query, intent=intent, results=results
        ):
            return self._clarification_turn(query=query, results=results, page_ids=page_ids)

        self._clear_pending_clarification()
        mode = "I fetched additional matches. " if from_more else ""
        best = results[0]
        answer = self._synthesized_answer_with_citations(intent, results)
        result_summary = self._summarize_result_set(results)
        sources = self._format_sources(results)
        likely_link = best.url or "No link available."
        return ConversationTurn(
            reply=self._speak(
                f"{mode}Direct answer:\n{answer}\n\n"
                "Most likely result:\n"
                f"- [1] {best.title}\n"
                f"- Link: {likely_link}\n\n"
                f"Top matches:\n{result_summary}\n\n"
                f"Sources:\n{sources}\n\n"
                "Search details:\n"
                f"- Results: {len(results)}{filter_scope}\n"
                f"- Intent: {self._intent_label(intent)}\n\n"
                "Next actions:\n"
                "- summarize result 2\n"
                "- what changed result 2\n"
                "- show more"
            ),
            results=results,
            page_ids=page_ids,
        )

    @staticmethod
    def _shorten(text: str, *, max_len: int = 180) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if len(compact) <= max_len:
            return compact
        clipped = compact[:max_len].rstrip()
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        return f"{clipped}..."

    def _synthesized_answer_with_citations(
        self, intent: QueryIntent, results: list[SearchResult]
    ) -> str:
        best = results[0]
        best_summary = self._shorten(best.summary, max_len=210)
        if intent.intent_label == "ownership":
            lead = (
                f"Ownership/responsibility appears most clearly in '{best.title}' [1]: "
                f"{best_summary}"
            )
        elif intent.intent_label == "how_to":
            lead = (
                f"The strongest procedure guidance is in '{best.title}' [1]: "
                f"{best_summary}"
            )
        elif intent.intent_label == "location":
            lead = (
                f"The most likely page for your request is '{best.title}' [1]: "
                f"{best_summary}"
            )
        elif intent.intent_label == "definition":
            lead = (
                f"The clearest definition/overview is in '{best.title}' [1]: "
                f"{best_summary}"
            )
        else:
            lead = f"The strongest match is '{best.title}' [1]: {best_summary}"

        if len(results) == 1:
            return lead

        second = results[1]
        second_summary = self._shorten(second.summary, max_len=150)
        support = (
            f"Supporting context from '{second.title}' [2]: {second_summary}"
        )
        if len(results) > 2:
            support += "\nAdditional corroboration is available in [3]."
        return f"{lead}\n{support}"

    def _summarize_result_set(self, results: list[SearchResult], *, max_items: int = 3) -> str:
        parts: list[str] = []
        for idx, item in enumerate(results[:max_items], start=1):
            snippet = self._shorten(item.summary, max_len=110)
            parts.append(f"{idx}) {item.title} [{idx}]\n   {snippet}")
        return "\n".join(parts)

    @staticmethod
    def _format_sources(results: list[SearchResult], *, max_items: int = 5) -> str:
        lines: list[str] = []
        for idx, item in enumerate(results[:max_items], start=1):
            lines.append(f"[{idx}] {item.title} - {item.url or 'No link available.'}")
        return "\n".join(lines)

    def _active_filter_scope(self) -> str:
        parts: list[str] = []
        if self.state.space_keys:
            if len(self.state.space_keys) == 1:
                parts.append(f"space {self.state.space_keys[0]}")
            else:
                parts.append(f"spaces {', '.join(self.state.space_keys)}")
        elif self.state.space_key:
            parts.append(f"space {self.state.space_key}")

        if self.state.modified_since and self.state.modified_before:
            if self.state.modified_since == self.state.modified_before:
                parts.append(f"date {self.state.modified_since}")
            else:
                parts.append(
                    f"modified {self.state.modified_since} to {self.state.modified_before}"
                )
        elif self.state.modified_since:
            parts.append(f"modified since {self.state.modified_since}")
        elif self.state.modified_before:
            parts.append(f"modified before {self.state.modified_before}")

        if self.state.exclude_terms:
            parts.append(f"excluding {', '.join(self.state.exclude_terms[:4])}")

        if not parts:
            return ""
        return f" with filters ({'; '.join(parts)})"

    def _result_detail_turn(self, index: int) -> ConversationTurn:
        if not self.state.last_results:
            return ConversationTurn(
                reply=self._speak(
                    "No prior results are available yet. Ask a search question first."
                ),
                results=[],
                page_ids=[],
            )
        if index < 1 or index > len(self.state.last_results):
            return ConversationTurn(
                reply=self._speak(
                    f"Result {index} is out of range. "
                    f"Available range is 1-{len(self.state.last_results)}."
                ),
                results=self.state.last_results,
                page_ids=self.state.last_page_ids,
            )

        item = self.state.last_results[index - 1]
        page_id = (
            self.state.last_page_ids[index - 1]
            if index - 1 < len(self.state.last_page_ids)
            else None
        )
        detailed_summary = self._detailed_page_summary(item=item, page_id=page_id)
        detail = [
            f"Result {index}: {item.title}.",
            f"Detailed summary: {detailed_summary}",
        ]
        if item.space_key:
            detail.append(f"Space: {item.space_key}.")
        if item.last_modified:
            detail.append(f"Last modified: {item.last_modified}.")
        if item.url:
            detail.append(f"Link: {item.url}")
        return ConversationTurn(
            reply=self._speak("\n".join(detail)),
            results=self.state.last_results,
            page_ids=self.state.last_page_ids,
        )

    def _result_changes_turn(self, index: int) -> ConversationTurn:
        if not self.state.last_results:
            return ConversationTurn(
                reply=self._speak(
                    "No prior results are available yet. Ask a search question first."
                ),
                results=[],
                page_ids=[],
            )
        if index < 1 or index > len(self.state.last_results):
            return ConversationTurn(
                reply=self._speak(
                    f"Result {index} is out of range. "
                    f"Available range is 1-{len(self.state.last_results)}."
                ),
                results=self.state.last_results,
                page_ids=self.state.last_page_ids,
            )
        if not self.state.previous_results:
            return ConversationTurn(
                reply=self._speak(
                    "I need a previous result set to compare against. "
                    "Run another related search, then ask what changed."
                ),
                results=self.state.last_results,
                page_ids=self.state.last_page_ids,
            )

        current = self.state.last_results[index - 1]
        current_page_id = (
            self.state.last_page_ids[index - 1]
            if index - 1 < len(self.state.last_page_ids)
            else None
        )
        prior = self._find_previous_match(current, current_page_id)
        if prior is None:
            return ConversationTurn(
                reply=self._speak(
                    f"Result {index} ({current.title}) did not appear in the previous result set, "
                    "so there is no direct prior snapshot to compare."
                ),
                results=self.state.last_results,
                page_ids=self.state.last_page_ids,
            )

        added, removed = self._summary_delta(current.summary, prior.summary)
        lines = [f"Result {index}: {current.title}."]
        if current.last_modified and prior.last_modified:
            if current.last_modified != prior.last_modified:
                lines.append(
                    "Last-modified changed from "
                    f"{prior.last_modified} to {current.last_modified}."
                )
            else:
                lines.append(f"Last-modified is unchanged at {current.last_modified}.")
        elif current.last_modified:
            lines.append(f"Current last-modified timestamp: {current.last_modified}.")

        if added:
            lines.append(f"New emphasis vs previous snapshot: {', '.join(added)}.")
        if removed:
            lines.append(f"Less-emphasized terms vs previous snapshot: {', '.join(removed)}.")
        if not added and not removed:
            lines.append(
                "No obvious topical change detected from the cached summaries."
            )
        if current.url:
            lines.append(f"Link: {current.url}")

        return ConversationTurn(
            reply=self._speak("\n".join(lines)),
            results=self.state.last_results,
            page_ids=self.state.last_page_ids,
        )

    def _find_previous_match(
        self, current: SearchResult, current_page_id: str | None
    ) -> SearchResult | None:
        for idx, prior in enumerate(self.state.previous_results):
            prior_page_id = (
                self.state.previous_page_ids[idx]
                if idx < len(self.state.previous_page_ids)
                else None
            )
            if current_page_id and prior_page_id and current_page_id == prior_page_id:
                return prior
            if current.url and prior.url and current.url == prior.url:
                return prior
            if current.title and prior.title and current.title.lower() == prior.title.lower():
                return prior
        return None

    def _summary_delta(self, current: str, previous: str) -> tuple[list[str], list[str]]:
        current_terms = self._keywords_for_compare(current)
        previous_terms = self._keywords_for_compare(previous)
        added = sorted(current_terms - previous_terms)[:6]
        removed = sorted(previous_terms - current_terms)[:6]
        return added, removed

    def _detailed_page_summary(self, *, item: SearchResult, page_id: str | None) -> str:
        if not page_id or not self.state.base_url:
            return item.summary
        if not (
            self.state.personal_access_token
            or (self.state.email and self.state.api_token)
        ):
            return item.summary

        try:
            agent = ConfluenceSearchAgent(
                base_url=self.state.base_url,
                personal_access_token=self.state.personal_access_token,
                email=self.state.email,
                api_token=self.state.api_token,
            )
            detailed = agent.generate_detailed_summary(
                SearchResult(
                    title=item.title,
                    url=item.url,
                    summary=item.summary,
                    space_key=item.space_key,
                    last_modified=item.last_modified,
                    page_id=page_id,
                ),
                query=self.state.last_query or item.title,
            )
            if detailed:
                return detailed
        except (ValueError, ConfluenceSearchError):
            return item.summary
        return item.summary

    @staticmethod
    def _keywords_for_compare(text: str) -> set[str]:
        tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]{3,}", text or "")
        }
        stop = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "into",
            "about",
            "result",
            "page",
            "summary",
        }
        return {token for token in tokens if token not in stop}

    def _compare_results_turn(self, first_idx: int, second_idx: int) -> ConversationTurn:
        if not self.state.last_results:
            return ConversationTurn(
                reply=self._speak(
                    "No prior results are available yet. Ask a search question first."
                ),
                results=[],
                page_ids=[],
            )
        max_idx = len(self.state.last_results)
        if (
            first_idx < 1
            or second_idx < 1
            or first_idx > max_idx
            or second_idx > max_idx
        ):
            return ConversationTurn(
                reply=self._speak(
                    f"Comparison indices are out of range. "
                    f"Available range is 1-{max_idx}."
                ),
                results=self.state.last_results,
                page_ids=self.state.last_page_ids,
            )
        if first_idx == second_idx:
            return ConversationTurn(
                reply=self._speak("Please provide two different results to compare."),
                results=self.state.last_results,
                page_ids=self.state.last_page_ids,
            )

        left = self.state.last_results[first_idx - 1]
        right = self.state.last_results[second_idx - 1]

        left_text = f"{left.title} {left.summary}"
        right_text = f"{right.title} {right.summary}"
        left_keywords = self._keywords_for_compare(left_text)
        right_keywords = self._keywords_for_compare(right_text)
        common = sorted(left_keywords & right_keywords)
        left_only = sorted(left_keywords - right_keywords)
        right_only = sorted(right_keywords - left_keywords)

        common_text = ", ".join(common[:6]) if common else "no strong shared keywords"
        left_only_text = ", ".join(left_only[:5]) if left_only else "no standout unique terms"
        right_only_text = ", ".join(right_only[:5]) if right_only else "no standout unique terms"

        reply = (
            f"Comparison of result {first_idx} and result {second_idx}:\n"
            f"- Result {first_idx}: '{left.title}'\n"
            f"- Result {second_idx}: '{right.title}'\n"
            f"- Common themes: {common_text}\n"
            f"- Result {first_idx} unique focus: {left_only_text}\n"
            f"- Result {second_idx} unique focus: {right_only_text}\n"
            "Next: ask 'summarize result X' for deeper detail."
        )
        return ConversationTurn(
            reply=self._speak(reply),
            results=self.state.last_results,
            page_ids=self.state.last_page_ids,
        )

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
            "index": idx,
            "title": item.title,
            "url": item.url,
            "summary": item.summary,
            "space_key": item.space_key,
            "last_modified": item.last_modified,
            "page_id": item.page_id,
        }
        for idx, item in enumerate(results, start=1)
    ]
