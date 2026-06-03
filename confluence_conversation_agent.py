#!/usr/bin/env python3
"""Conversation layer for Confluence search."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
    limit: int = DEFAULT_LIMIT
    chat_model_api_key: str | None = None
    chat_model_name: str = "gpt-4.1-mini"
    chat_model_api_base: str = "https://api.openai.com/v1"
    chat_model_enabled: bool = False
    last_query: str = ""
    last_results: list[SearchResult] = field(default_factory=list)
    last_page_ids: list[str | None] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)


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
        self._append_history("user", text)

        if text.lower().startswith("chat:"):
            prompt = text.split(":", 1)[1].strip()
            return self._chat_only_turn(prompt or text)

        self._apply_inline_filters(text)
        detail_idx = self._requested_result_index(text)
        if detail_idx is not None:
            return self._result_detail_turn(detail_idx)

        compare_indices = self._requested_compare_indices(text)
        if compare_indices is not None:
            return self._compare_results_turn(compare_indices[0], compare_indices[1])

        if not self.state.base_url:
            if self._has_chat_model():
                return self._chat_only_turn(text)
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
            if self._has_chat_model():
                return self._chat_only_turn(text)
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
            return self._search_turn(self.state.last_query, from_more=True)

        if self._is_repeat_request(text):
            if not self.state.last_query:
                return ConversationTurn(
                    reply=self._speak("There is no previous query to repeat yet."),
                    results=[],
                    page_ids=[],
                )
            return self._search_turn(self.state.last_query, from_more=False)

        return self._search_turn(text, from_more=False)

    def _append_history(self, role: str, content: str) -> None:
        clean = re.sub(r"\s+", " ", str(content or "")).strip()
        if not clean:
            return
        self.state.conversation_history.append({"role": role, "content": clean})
        self.state.conversation_history = self.state.conversation_history[-16:]

    def _chat_model_config(self) -> tuple[str, str, str] | None:
        api_key = (
            self.state.chat_model_api_key
            or os.getenv("CONFLUENCE_CHAT_MODEL_API_KEY", "").strip()
        )
        model = (
            self.state.chat_model_name.strip()
            if self.state.chat_model_name
            else os.getenv("CONFLUENCE_CHAT_MODEL", "gpt-4.1-mini").strip()
        )
        api_base = (
            self.state.chat_model_api_base.strip()
            if self.state.chat_model_api_base
            else os.getenv("CONFLUENCE_CHAT_MODEL_API_BASE", "https://api.openai.com/v1").strip()
        )
        env_enabled = os.getenv("CONFLUENCE_CHAT_MODEL_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        enabled = self.state.chat_model_enabled or env_enabled or bool(self.state.chat_model_api_key)
        if not enabled or not api_key or not model:
            return None
        return api_key, model, api_base.rstrip("/")

    def _has_chat_model(self) -> bool:
        return self._chat_model_config() is not None

    @staticmethod
    def _chat_completion_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                chunks: list[str] = []
                for entry in content:
                    if isinstance(entry, dict) and entry.get("type") == "text":
                        text = entry.get("text")
                        if isinstance(text, str):
                            chunks.append(text)
                return "\n".join(chunks).strip()
        text = first.get("text")
        if isinstance(text, str):
            return text.strip()
        return ""

    def _call_chat_model(self, *, system_prompt: str, user_prompt: str) -> str | None:
        config = self._chat_model_config()
        if config is None:
            return None
        api_key, model, api_base = config
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for turn in self.state.conversation_history[-8:]:
            role = turn.get("role", "")
            content = turn.get("content", "")
            if role in {"user", "assistant"} and isinstance(content, str) and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.25,
            "max_tokens": 700,
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url=f"{api_base}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urlopen(request, timeout=35) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            text = self._chat_completion_text(parsed)
            return text or None
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError, OSError):
            return None

    @staticmethod
    def _sources_for_prompt(results: list[SearchResult], *, max_items: int = 5) -> str:
        lines: list[str] = []
        for idx, item in enumerate(results[:max_items], start=1):
            lines.append(
                f"[{idx}] title={item.title} | space={item.space_key or 'n/a'} | "
                f"summary={item.summary} | url={item.url or 'n/a'}"
            )
        return "\n".join(lines)

    def _chat_only_turn(self, text: str) -> ConversationTurn:
        model_text = self._call_chat_model(
            system_prompt=(
                "You are Einstein, a helpful conversational assistant for engineering teams. "
                "Be concise, practical, and friendly. If no Confluence sources are provided, "
                "be explicit that you are answering without document grounding."
            ),
            user_prompt=text,
        )
        if model_text:
            reply = self._speak(model_text)
        else:
            reply = self._speak(
                "I can do conversational responses when a chat model is configured. "
                "Set chat model settings in the UI (API key + model) or via "
                "CONFLUENCE_CHAT_MODEL_* environment variables."
            )
        self._append_history("assistant", reply.replace(f"{BOT_NAME}: ", "", 1))
        return ConversationTurn(reply=reply, results=self.state.last_results, page_ids=self.state.last_page_ids)

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

        if "chat_model_api_key" in settings:
            raw = str(settings.get("chat_model_api_key") or "").strip()
            self.state.chat_model_api_key = raw or None
        if "chat_model_name" in settings:
            raw = str(settings.get("chat_model_name") or "").strip()
            if raw:
                self.state.chat_model_name = raw
        if "chat_model_api_base" in settings:
            raw = str(settings.get("chat_model_api_base") or "").strip()
            if raw:
                self.state.chat_model_api_base = raw
        if "chat_model_enabled" in settings:
            self.state.chat_model_enabled = bool(settings.get("chat_model_enabled"))

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

        in_space = re.search(r"\bin\s+([A-Za-z][A-Za-z0-9_]{1,15})\s+space\b", text, re.IGNORECASE)
        if in_space:
            self.state.space_key = in_space.group(1).upper()
        else:
            explicit_space = re.search(
                r"\bspace\s+([A-Za-z][A-Za-z0-9_]{1,15})\b", text, re.IGNORECASE
            )
            if explicit_space:
                self.state.space_key = explicit_space.group(1).upper()

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

    def _search_turn(self, query: str, *, from_more: bool) -> ConversationTurn:
        agent = ConfluenceSearchAgent(
            base_url=self.state.base_url,
            personal_access_token=self.state.personal_access_token,
            email=self.state.email,
            api_token=self.state.api_token,
        )
        results = agent.search(query=query, limit=self.state.limit, space_key=self.state.space_key)
        page_ids = [item.page_id for item in results]
        self.state.last_query = query
        self.state.last_results = results
        self.state.last_page_ids = page_ids

        intent = ConfluenceSearchAgent._query_intent(query)
        scope = f" in space {self.state.space_key}" if self.state.space_key else ""
        if not results:
            turn = ConversationTurn(
                reply=self._speak(
                    f"I couldn't find matching pages{scope} for '{query}'. "
                    "Try refining the question, changing the space filter, or asking for more general terms."
                ),
                results=[],
                page_ids=[],
            )
            self._append_history("assistant", turn.reply.replace(f"{BOT_NAME}: ", "", 1))
            return turn

        mode = "I fetched additional matches. " if from_more else ""
        best = results[0]
        answer = self._answer_from_results(intent, best)
        result_summary = self._summarize_result_set(results)
        likely_link = best.url or "No link available."
        fallback_reply = (
            f"{mode}Answer: {answer}\n"
            f"Most likely result link: {likely_link}\n"
            f"Result summary:\n{result_summary}\n"
            f"I found {len(results)} page(s){scope} for '{query}' "
            f"(intent: {self._intent_label(intent)}).\n"
            "You can ask 'summarize result 2' or 'show more'."
        )

        conversational = self._call_chat_model(
            system_prompt=(
                "You are Einstein, a Confluence copilot-style assistant. "
                "Respond conversationally but stay grounded to supplied sources. "
                "Use inline citations like [1], [2] when citing source bullets."
            ),
            user_prompt=(
                f"User question: {query}\n"
                f"Intent: {self._intent_label(intent)}\n"
                f"Result count: {len(results)}{scope}\n\n"
                "Sources:\n"
                f"{self._sources_for_prompt(results)}\n\n"
                "Write a practical answer with:\n"
                "1) direct answer\n"
                "2) why this is likely correct\n"
                "3) one suggested follow-up question."
            ),
        )
        if conversational:
            reply_text = (
                f"{mode}{conversational}\n\n"
                f"Top source link: {likely_link}\n"
                "Tip: ask 'summarize result 2' or 'compare #1 and #2'."
            )
        else:
            reply_text = fallback_reply

        turn = ConversationTurn(
            reply=self._speak(reply_text),
            results=results,
            page_ids=page_ids,
        )
        self._append_history("assistant", turn.reply.replace(f"{BOT_NAME}: ", "", 1))
        return turn

    @staticmethod
    def _shorten(text: str, *, max_len: int = 180) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if len(compact) <= max_len:
            return compact
        clipped = compact[:max_len].rstrip()
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        return f"{clipped}..."

    def _answer_from_results(self, intent: QueryIntent, best: SearchResult) -> str:
        best_summary = self._shorten(best.summary, max_len=210)
        if intent.intent_label == "ownership":
            return (
                f"The most likely owner/responsibility info is in '{best.title}': "
                f"{best_summary}"
            )
        if intent.intent_label == "how_to":
            return f"The best procedure-oriented answer appears in '{best.title}': {best_summary}"
        if intent.intent_label == "location":
            return f"The most likely page you are looking for is '{best.title}': {best_summary}"
        if intent.intent_label == "definition":
            return f"The clearest definition/overview is in '{best.title}': {best_summary}"
        return f"The strongest match is '{best.title}': {best_summary}"

    def _summarize_result_set(self, results: list[SearchResult], *, max_items: int = 3) -> str:
        parts: list[str] = []
        for idx, item in enumerate(results[:max_items], start=1):
            snippet = self._shorten(item.summary, max_len=110)
            parts.append(f"{idx}) {item.title} - {snippet}")
        return "\n".join(parts)

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
        turn = ConversationTurn(
            reply=self._speak(" ".join(detail)),
            results=self.state.last_results,
            page_ids=self.state.last_page_ids,
        )
        self._append_history("assistant", turn.reply.replace(f"{BOT_NAME}: ", "", 1))
        return turn

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
            f"Comparison of result {first_idx} and result {second_idx}: "
            f"Result {first_idx} is '{left.title}', and result {second_idx} is '{right.title}'. "
            f"Common themes: {common_text}. "
            f"Result {first_idx} unique focus: {left_only_text}. "
            f"Result {second_idx} unique focus: {right_only_text}. "
            "Ask for 'summarize result X' if you want deeper detail."
        )
        turn = ConversationTurn(
            reply=self._speak(reply),
            results=self.state.last_results,
            page_ids=self.state.last_page_ids,
        )
        self._append_history("assistant", turn.reply.replace(f"{BOT_NAME}: ", "", 1))
        return turn

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
