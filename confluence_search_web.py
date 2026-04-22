#!/usr/bin/env python3
"""Local web UI for searching Confluence pages."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import time
from typing import Any

from confluence_conversation_agent import (
    ConfluenceConversationAgent,
    ConversationState,
    serialize_results,
)
from confluence_search_agent import ConfluenceSearchAgent, ConfluenceSearchError

SESSION_COOKIE = "confluence_chat_session"
SESSION_TTL_SECONDS = 60 * 60 * 8
SESSION_STATES: dict[str, dict[str, Any]] = {}

INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>Confluence Search</title>
    <style>
      :root {
        --exp-blue: #002f87;
        --exp-cyan: #00a3e0;
        --exp-violet: #4d148c;
        --exp-indigo: #243a9a;
        --surface: #ffffff;
        --surface-soft: #f5f8ff;
        --text: #0e1735;
        --muted: #4a5680;
        --border: #d7dff4;
        --ok: #0f7b4d;
        --warn: #9a6400;
        --err: #b00035;
        --shadow: 0 12px 32px rgba(9, 28, 82, 0.16);
        font-family: "Inter", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      }
      body {
        margin: 0;
        min-height: 100vh;
        color: var(--text);
        background:
          radial-gradient(circle at 18% 18%, rgba(0, 163, 224, 0.45), transparent 40%),
          radial-gradient(circle at 82% 20%, rgba(77, 20, 140, 0.35), transparent 40%),
          linear-gradient(140deg, #00184d 0%, #002f87 45%, #0646b9 100%);
        position: relative;
      }
      body::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.04), transparent 28%);
        z-index: 0;
      }
      .bg-logo {
        position: fixed;
        right: clamp(1rem, 3vw, 2rem);
        bottom: clamp(1rem, 3vw, 2rem);
        width: min(46vw, 520px);
        height: auto;
        opacity: 0.13;
        pointer-events: none;
        z-index: 0;
      }
      .shell {
        max-width: 1060px;
        margin: 0 auto;
        padding: 2.5rem 1.5rem 3rem;
        position: relative;
        z-index: 1;
      }
      .hero {
        margin-bottom: 1.25rem;
        color: #f3f7ff;
      }
      .hero-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.8rem;
      }
      h1 {
        margin: 0;
        font-size: 2rem;
        font-weight: 700;
      }
      .hero p {
        margin: 0.6rem 0 0;
        max-width: 68ch;
        color: #d7e4ff;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        font-size: 0.75rem;
        letter-spacing: 0.01em;
        font-weight: 700;
        padding: 0.35rem 0.62rem;
        border-radius: 999px;
        border: 1px solid rgba(255, 255, 255, 0.35);
        color: #eaf2ff;
        background: rgba(0, 0, 0, 0.15);
      }
      .hero-badges {
        margin-top: 0.8rem;
        display: flex;
        flex-wrap: wrap;
        gap: 0.45rem;
      }
      .hero-badges .badge {
        font-weight: 600;
        background: rgba(255, 255, 255, 0.1);
      }
      .panel {
        background: var(--surface);
        border-radius: 18px;
        border: 1px solid rgba(255, 255, 255, 0.68);
        box-shadow: var(--shadow);
        backdrop-filter: blur(3px);
        padding: 1.1rem 1.15rem 1.25rem;
        margin-bottom: 1rem;
      }
      .panel-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 0.8rem;
        margin-bottom: 0.85rem;
      }
      .panel-title {
        margin: 0;
        font-size: 0.98rem;
        font-weight: 700;
        color: var(--exp-blue);
        letter-spacing: 0.01em;
      }
      .panel-subtle {
        font-size: 0.79rem;
        color: var(--muted);
      }
      .settings-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.9rem 1rem;
        margin: 0;
      }
      .full { grid-column: 1 / -1; }
      label {
        display: flex;
        flex-direction: column;
        font-size: 0.85rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        color: var(--muted);
        gap: 0.4rem;
      }
      input {
        border: 1px solid var(--border);
        background: var(--surface-soft);
        color: var(--text);
        border-radius: 10px;
        padding: 0.68rem 0.72rem;
        font-size: 0.95rem;
        outline: none;
      }
      input:focus {
        border-color: var(--exp-cyan);
        box-shadow: 0 0 0 3px rgba(0, 163, 224, 0.18);
      }
      .actions {
        display: flex;
        align-items: center;
        gap: 0.8rem;
      }
      button {
        border: 0;
        border-radius: 999px;
        padding: 0.72rem 1.2rem;
        font-size: 0.94rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        color: white;
        background: linear-gradient(100deg, var(--exp-blue), var(--exp-violet));
        box-shadow: 0 8px 20px rgba(18, 48, 122, 0.28);
      }
      button:hover {
        filter: brightness(1.04);
      }
      button {
        cursor: pointer;
      }
      .hint {
        font-size: 0.82rem;
        color: var(--muted);
      }
      #status {
        min-height: 1rem;
        margin: 0.2rem 0 0.4rem;
        font-weight: 600;
        font-size: 0.83rem;
        border-radius: 999px;
        padding: 0.33rem 0.7rem;
        display: inline-flex;
        align-items: center;
        background: rgba(0, 47, 135, 0.08);
        color: var(--exp-blue);
      }
      #status[data-tone="ok"] {
        background: rgba(15, 123, 77, 0.12);
        color: var(--ok);
      }
      #status[data-tone="error"] {
        background: rgba(176, 0, 53, 0.12);
        color: var(--err);
      }
      .loading-wrap {
        display: none;
        align-items: center;
        gap: 0.8rem;
        margin-top: 0.65rem;
        padding: 0.6rem 0.75rem;
        border-radius: 10px;
        background: linear-gradient(
          90deg,
          rgba(0, 47, 135, 0.08),
          rgba(77, 20, 140, 0.08)
        );
      }
      .loading-wrap.active {
        display: inline-flex;
      }
      .loading-wrap img {
        width: 52px;
        height: 52px;
        object-fit: cover;
        border-radius: 8px;
        border: 1px solid rgba(0, 47, 135, 0.2);
      }
      .loading-wrap span {
        color: var(--muted);
        font-size: 0.9rem;
        font-weight: 600;
      }
      #results {
        display: grid;
        gap: 0.85rem;
      }
      .results-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 0.8rem;
        margin-bottom: 0.2rem;
      }
      .results-meta {
        font-size: 0.78rem;
        color: var(--muted);
      }
      .empty-results {
        margin: 0.4rem 0 0;
        border: 1px dashed var(--border);
        border-radius: 12px;
        padding: 0.9rem;
        background: #fafcff;
        color: var(--muted);
      }
      .result {
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 1rem;
        background: white;
        box-shadow: 0 4px 14px rgba(7, 23, 64, 0.06);
      }
      .result-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 0.7rem;
      }
      .result h3 {
        margin: 0;
        font-size: 1.05rem;
      }
      .rank-pill {
        font-size: 0.74rem;
        font-weight: 700;
        color: var(--exp-indigo);
        background: rgba(36, 58, 154, 0.08);
        border: 1px solid rgba(36, 58, 154, 0.16);
        border-radius: 999px;
        padding: 0.21rem 0.5rem;
        white-space: nowrap;
      }
      .result a {
        color: var(--exp-blue);
        text-decoration: none;
      }
      .result a:hover {
        text-decoration: underline;
      }
      .meta {
        margin: 0.45rem 0 0.5rem;
        font-size: 0.8rem;
        color: var(--muted);
      }
      .summary {
        margin: 0;
        color: var(--text);
        line-height: 1.5;
      }
      .result-actions {
        display: flex;
        gap: 0.45rem;
        flex-wrap: wrap;
        margin-top: 0.75rem;
      }
      .tiny-btn {
        padding: 0.36rem 0.72rem;
        font-size: 0.76rem;
        border-radius: 999px;
        background: #eef2ff;
        color: var(--exp-blue);
        border: 1px solid #ced8ff;
        box-shadow: none;
      }
      .tiny-btn:hover:not(:disabled) {
        background: #e3ebff;
        filter: none;
      }
      .tiny-btn:disabled {
        cursor: not-allowed;
        opacity: 0.55;
      }
      .chat-wrap {
        display: grid;
        gap: 0.8rem;
      }
      .prompt-chips {
        display: flex;
        gap: 0.45rem;
        flex-wrap: wrap;
      }
      .chip-btn {
        border-radius: 999px;
        border: 1px solid rgba(0, 47, 135, 0.16);
        background: rgba(0, 47, 135, 0.06);
        color: var(--exp-blue);
        font-size: 0.76rem;
        font-weight: 600;
        padding: 0.29rem 0.62rem;
        cursor: pointer;
      }
      .chip-btn:hover {
        background: rgba(0, 47, 135, 0.1);
      }
      #chat-log {
        max-height: 320px;
        overflow: auto;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: var(--surface-soft);
        padding: 0.75rem;
        display: grid;
        gap: 0.55rem;
      }
      .bubble {
        padding: 0.6rem 0.72rem;
        border-radius: 10px;
        line-height: 1.45;
        font-size: 0.92rem;
        white-space: pre-wrap;
      }
      .bubble.user {
        background: rgba(0, 163, 224, 0.12);
        border: 1px solid rgba(0, 163, 224, 0.26);
      }
      .bubble.assistant {
        background: rgba(77, 20, 140, 0.08);
        border: 1px solid rgba(77, 20, 140, 0.2);
      }
      .bubble.assistant a {
        color: var(--exp-blue);
        font-weight: 600;
        text-decoration: underline;
      }
      .bubble.assistant a:hover {
        color: var(--exp-violet);
      }
      .chat-form {
        display: flex;
        gap: 0.65rem;
        align-items: center;
      }
      .chat-toolbar {
        display: flex;
        gap: 0.55rem;
        flex-wrap: wrap;
      }
      .chat-toolbar button {
        padding: 0.52rem 0.9rem;
        font-size: 0.82rem;
        border-radius: 999px;
      }
      .ghost-btn {
        background: #eef2ff;
        color: var(--exp-blue);
        border: 1px solid #ced8ff;
        box-shadow: none;
      }
      .ghost-btn:hover {
        background: #e3ebff;
        filter: none;
      }
      .chat-form input {
        flex: 1;
      }
      .chat-form button:disabled,
      .chat-toolbar button:disabled,
      .result-actions button:disabled {
        opacity: 0.62;
        cursor: not-allowed;
      }
      @media (max-width: 760px) {
        .settings-grid {
          grid-template-columns: 1fr;
        }
        .hero-top {
          align-items: flex-start;
          flex-direction: column;
        }
        .results-head {
          align-items: flex-start;
          flex-direction: column;
        }
        .chat-form {
          flex-direction: column;
          align-items: stretch;
        }
      }
    </style>
  </head>
  <body>
    <svg class="bg-logo" viewBox="0 0 640 200" aria-hidden="true">
      <defs>
        <linearGradient id="expLogoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#ffffff" />
          <stop offset="100%" stop-color="#d7e4ff" />
        </linearGradient>
      </defs>
      <text
        x="18"
        y="130"
        font-family="Inter, Segoe UI, Arial, sans-serif"
        font-size="92"
        font-weight="700"
        letter-spacing="0.5"
        fill="url(#expLogoGrad)"
      >Experian</text>
      <circle cx="440" cy="64" r="10" fill="#00a3e0" />
      <circle cx="466" cy="49" r="8" fill="#8e4bd6" />
      <circle cx="466" cy="79" r="8" fill="#e20074" />
      <circle cx="492" cy="64" r="7" fill="#5c2d91" />
    </svg>
    <main class="shell">
      <header class="hero">
        <div class="hero-top">
          <h1>Confluence Intelligence Search</h1>
          <span class="badge">Einstein Ready</span>
        </div>
        <p>
          Ask natural questions, get grounded answers, and move faster with concise
          AI-generated page summaries.
        </p>
        <div class="hero-badges">
          <span class="badge">Citations</span>
          <span class="badge">Smart Filters</span>
          <span class="badge">Follow-up Actions</span>
        </div>
      </header>

      <section class="panel">
        <div class="panel-head">
          <h2 class="panel-title">Connection Settings</h2>
          <span class="panel-subtle">Used only for live API calls</span>
        </div>
        <div class="settings-grid">
          <label class="full">
            Confluence Base URL
            <input id="base-url" name="base_url" placeholder="https://your-domain.atlassian.net" required />
          </label>

          <label class="full">
            Personal Access Token (recommended)
            <input id="personal-access-token" name="personal_access_token" type="password" />
          </label>

          <label>
            Email (optional fallback for API token auth)
            <input id="email" name="email" placeholder="you@company.com" />
          </label>
          <label>
            API Token (optional fallback)
            <input id="api-token" name="api_token" type="password" />
          </label>

          <span class="hint full">
            Credentials are used only for live API calls and not persisted. Einstein infers filters from your question, including space(s), result count, date ranges, and exclude terms (for example: "top 5 in ENG and OPS spaces since 2026-01-01 excluding draft").
          </span>
        </div>
      </section>

      <section class="panel">
        <div class="chat-wrap">
          <div class="panel-head">
            <h2 class="panel-title">Einstein Chat</h2>
            <span class="panel-subtle">Conversation-aware Confluence assistant</span>
          </div>
          <div id="prompt-chips" class="prompt-chips" aria-label="Quick prompts">
            <button type="button" class="chip-btn" data-message="who owns 4x9?">Who owns 4x9?</button>
            <button type="button" class="chip-btn" data-message="where can I find the 4x9 status page?">Find 4x9 status</button>
            <button type="button" class="chip-btn" data-message="top 5 in ENG and OPS spaces">Top 5 in ENG + OPS</button>
            <button type="button" class="chip-btn" data-message="summarize result 1">Summarize #1</button>
          </div>
          <div id="chat-log" aria-live="polite"></div>
          <form id="chat-form" class="chat-form">
            <input
              id="chat-message"
              name="chat_message"
              placeholder="Ask Einstein: who owns this service, summarize result 2, compare #1 and #3, or show more"
              required
            />
            <button id="chat-send-btn" type="submit">Send</button>
          </form>
          <div class="chat-toolbar">
            <button type="button" id="clear-chat-btn" class="ghost-btn">Clear Conversation</button>
            <button type="button" id="export-chat-btn" class="ghost-btn">Export Transcript</button>
          </div>
          <span class="hint">Conversation remembers context in this browser session.</span>
        </div>
      </section>

      <section class="panel">
        <div class="results-head">
          <h2 class="panel-title">Results</h2>
          <span id="results-meta" class="results-meta">No results yet.</span>
        </div>
        <div id="status" aria-live="polite"></div>
        <div id="loading-indicator" class="loading-wrap" aria-hidden="true">
          <img
            src="https://i.makeagif.com/media/8-01-2019/lreogQ.gif"
            alt="Sonic the Hedgehog waiting while search runs"
          />
          <span>Sonic is waiting while we search Confluence...</span>
        </div>
        <div id="results"></div>
      </section>
    </main>

    <script>
      const statusEl = document.getElementById("status");
      const loadingEl = document.getElementById("loading-indicator");
      const resultsEl = document.getElementById("results");
      const chatLogEl = document.getElementById("chat-log");
      const chatFormEl = document.getElementById("chat-form");
      const chatMessageEl = document.getElementById("chat-message");
      const clearChatBtnEl = document.getElementById("clear-chat-btn");
      const exportChatBtnEl = document.getElementById("export-chat-btn");
      const promptChipContainerEl = document.getElementById("prompt-chips");
      const resultsMetaEl = document.getElementById("results-meta");
      const chatSendBtnEl = document.getElementById("chat-send-btn");
      const settingsEls = {
        base_url: document.getElementById("base-url"),
        personal_access_token: document.getElementById("personal-access-token"),
        email: document.getElementById("email"),
        api_token: document.getElementById("api-token"),
      };
      const SETTINGS_STORAGE_KEY = "confluence-ui-settings-v1";
      const chatTranscript = [];
      let lastRenderedResults = [];
      let settingsLoadedFromStorage = false;

      function status(message, tone = "info") {
        statusEl.textContent = String(message || "");
        statusEl.dataset.tone =
          tone === "error" || tone === "ok" ? tone : "info";
      }

      function setLoading(isLoading) {
        if (isLoading) {
          loadingEl.classList.add("active");
          loadingEl.setAttribute("aria-hidden", "false");
          return;
        }
        loadingEl.classList.remove("active");
        loadingEl.setAttribute("aria-hidden", "true");
      }

      function appendChatBubble(role, text) {
        const bubble = document.createElement("div");
        bubble.className = "bubble " + role;
        if (role === "assistant") {
          bubble.innerHTML = linkifyText(text);
        } else {
          bubble.textContent = text;
        }
        chatLogEl.appendChild(bubble);
        chatLogEl.scrollTop = chatLogEl.scrollHeight;
        chatTranscript.push({
          role,
          text,
          ts: new Date().toISOString(),
        });
      }

      function escapeHtml(text) {
        return String(text || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#39;");
      }

      function linkifyText(text) {
        const escaped = escapeHtml(text);
        return escaped.replace(/https?:\\/\\/[^\\s<>"']+/g, (rawUrl) => {
          let url = rawUrl;
          let trailing = "";
          while (url && /[).,!?]$/.test(url)) {
            trailing = url.slice(-1) + trailing;
            url = url.slice(0, -1);
          }
          return `<a href="${url}" target="_blank" rel="noreferrer noopener">${url}</a>${trailing}`;
        });
      }

      function currentSettings() {
        return {
          base_url: settingsEls.base_url.value.trim(),
          personal_access_token: settingsEls.personal_access_token.value,
          email: settingsEls.email.value.trim(),
          api_token: settingsEls.api_token.value,
        };
      }

      function persistableSettingsSnapshot() {
        const payload = currentSettings();
        return {
          base_url: payload.base_url,
          email: payload.email,
        };
      }

      function saveSettingsToStorage() {
        if (!settingsLoadedFromStorage) return;
        try {
          localStorage.setItem(
            SETTINGS_STORAGE_KEY,
            JSON.stringify(persistableSettingsSnapshot())
          );
        } catch (_) {
          // Ignore storage errors in restrictive browser contexts.
        }
      }

      function loadSettingsFromStorage() {
        try {
          const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
          if (!raw) return;
          const data = JSON.parse(raw);
          if (data && typeof data === "object") {
            if (typeof data.base_url === "string") {
              settingsEls.base_url.value = data.base_url;
            }
            if (typeof data.email === "string") {
              settingsEls.email.value = data.email;
            }
          }
        } catch (_) {
          // Ignore malformed storage payloads.
        } finally {
          settingsLoadedFromStorage = true;
        }
      }

      function setButtonsDisabled(isDisabled) {
        const formButtons = chatFormEl.querySelectorAll("button");
        for (const button of formButtons) button.disabled = isDisabled;
        clearChatBtnEl.disabled = isDisabled;
        exportChatBtnEl.disabled = isDisabled;
      }

      function formatTimestamp(isoValue) {
        if (!isoValue) return "";
        const parsed = new Date(isoValue);
        if (Number.isNaN(parsed.valueOf())) return isoValue;
        return parsed.toLocaleString();
      }

      function renderResults(items) {
        resultsEl.textContent = "";
        lastRenderedResults = Array.isArray(items) ? items : [];
        if (!lastRenderedResults.length) {
          resultsMetaEl.textContent = "No matching pages found.";
          const empty = document.createElement("p");
          empty.className = "empty-results";
          empty.textContent = "No matching pages found for the latest request.";
          resultsEl.appendChild(empty);
          return;
        }
        resultsMetaEl.textContent =
          lastRenderedResults.length === 1
            ? "1 result"
            : `${lastRenderedResults.length} results`;

        for (const item of lastRenderedResults) {
          const box = document.createElement("section");
          box.className = "result";

          const head = document.createElement("div");
          head.className = "result-head";
          const title = document.createElement("h3");
          if (item.url) {
            const a = document.createElement("a");
            a.href = item.url;
            a.target = "_blank";
            a.rel = "noreferrer noopener";
            a.textContent = item.title;
            title.appendChild(a);
          } else {
            title.textContent = item.title;
          }
          head.appendChild(title);

          const resultIndex = Number(item.index) || 0;
          if (resultIndex > 0) {
            const rank = document.createElement("span");
            rank.className = "rank-pill";
            rank.textContent = "#" + resultIndex;
            head.appendChild(rank);
          }
          box.appendChild(head);

          const meta = document.createElement("div");
          meta.className = "meta";
          const bits = [];
          if (item.space_key) bits.push("Space: " + item.space_key);
          if (item.last_modified) {
            bits.push("Last Modified: " + formatTimestamp(item.last_modified));
          }
          meta.textContent = bits.join(" | ");
          if (bits.length > 0) box.appendChild(meta);

          if (item.summary) {
            const summary = document.createElement("p");
            summary.className = "summary";
            summary.textContent = item.summary;
            box.appendChild(summary);
          }

          if (resultIndex > 0) {
            const actions = document.createElement("div");
            actions.className = "result-actions";

            const explainBtn = document.createElement("button");
            explainBtn.type = "button";
            explainBtn.className = "tiny-btn result-action-btn";
            explainBtn.dataset.action = "summarize";
            explainBtn.dataset.index = String(resultIndex);
            explainBtn.textContent = "Explain";
            actions.appendChild(explainBtn);

            const compareBtn = document.createElement("button");
            compareBtn.type = "button";
            compareBtn.className = "tiny-btn result-action-btn";
            compareBtn.dataset.action = "compare";
            compareBtn.dataset.index = String(resultIndex);
            const hasCompareTarget = lastRenderedResults.length > 1;
            if (!hasCompareTarget) {
              compareBtn.disabled = true;
              compareBtn.textContent = "Compare";
            } else if (resultIndex === 1) {
              compareBtn.dataset.compareWith = "2";
              compareBtn.textContent = "Compare vs #2";
            } else {
              compareBtn.dataset.compareWith = "1";
              compareBtn.textContent = "Compare vs #1";
            }
            actions.appendChild(compareBtn);

            const changedBtn = document.createElement("button");
            changedBtn.type = "button";
            changedBtn.className = "tiny-btn result-action-btn";
            changedBtn.dataset.action = "changed";
            changedBtn.dataset.index = String(resultIndex);
            changedBtn.textContent = "What changed";
            actions.appendChild(changedBtn);

            if (item.url) {
              const copyBtn = document.createElement("button");
              copyBtn.type = "button";
              copyBtn.className = "tiny-btn result-action-btn";
              copyBtn.dataset.action = "copy-link";
              copyBtn.dataset.url = item.url;
              copyBtn.textContent = "Copy link";
              actions.appendChild(copyBtn);
            }
            box.appendChild(actions);
          }
          resultsEl.appendChild(box);
        }
      }

      async function sendChatMessage(message, options = {}) {
        const { echoUser = true } = options;
        const trimmed = String(message || "").trim();
        if (!trimmed) return;
        if (echoUser) {
          appendChatBubble("user", trimmed);
        }
        status("Conversation search running...", "info");
        setLoading(true);
        setButtonsDisabled(true);

        const payload = {
          message: trimmed,
          settings: currentSettings(),
        };

        try {
          const response = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data.error || "Conversation request failed.");
          }
          appendChatBubble("assistant", data.reply || "Done.");
          renderResults(data.results || []);
          status("Conversation updated.", "ok");
        } catch (err) {
          appendChatBubble("assistant", err.message || "Conversation request failed.");
          status(err.message || "Conversation request failed.", "error");
        } finally {
          setLoading(false);
          setButtonsDisabled(false);
        }
      }

      chatFormEl.addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = chatMessageEl.value.trim();
        if (!message) return;
        chatMessageEl.value = "";
        await sendChatMessage(message, { echoUser: true });
      });

      resultsEl.addEventListener("click", async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const button = target.closest(".result-action-btn");
        if (!(button instanceof HTMLButtonElement) || button.disabled) return;
        const action = button.dataset.action || "";
        if (action === "copy-link") {
          const rawUrl = String(button.dataset.url || "");
          if (!rawUrl) return;
          try {
            await navigator.clipboard.writeText(rawUrl);
            status("Link copied to clipboard.", "ok");
          } catch (_) {
            status("Unable to copy link in this browser context.", "error");
          }
          return;
        }

        const idx = Number(button.dataset.index || "0");
        if (!idx) return;
        if (action === "summarize") {
          await sendChatMessage(`summarize result ${idx}`, { echoUser: true });
          return;
        }
        if (action === "compare") {
          const compareWith = Number(button.dataset.compareWith || "0");
          if (!compareWith || compareWith === idx) return;
          await sendChatMessage(
            `compare result ${idx} and result ${compareWith}`,
            { echoUser: true }
          );
          return;
        }
        if (action === "changed") {
          await sendChatMessage(`what changed result ${idx}`, { echoUser: true });
        }
      });

      clearChatBtnEl.addEventListener("click", async () => {
        setLoading(true);
        setButtonsDisabled(true);
        try {
          const response = await fetch("/api/chat/clear", { method: "POST" });
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data.error || "Could not clear conversation.");
          }
          chatLogEl.textContent = "";
          chatTranscript.length = 0;
          resultsEl.textContent = "";
          resultsMetaEl.textContent = "No results yet.";
          appendChatBubble("assistant", data.reply || "Conversation cleared.");
          status("Conversation cleared.", "ok");
        } catch (err) {
          status(err.message || "Could not clear conversation.", "error");
        } finally {
          setLoading(false);
          setButtonsDisabled(false);
        }
      });

      exportChatBtnEl.addEventListener("click", () => {
        if (!chatTranscript.length) {
          status("No transcript to export yet.", "error");
          return;
        }
        const exportPayload = {
          exported_at: new Date().toISOString(),
          transcript: chatTranscript,
        };
        const blob = new Blob([JSON.stringify(exportPayload, null, 2)], {
          type: "application/json",
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const stamp = new Date().toISOString().replace(/[:.]/g, "-");
        a.href = url;
        a.download = `confluence-conversation-${stamp}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        status("Transcript exported.", "ok");
      });

      promptChipContainerEl.addEventListener("click", async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const button = target.closest(".chip-btn");
        if (!(button instanceof HTMLButtonElement)) return;
        const message = String(button.dataset.message || "").trim();
        if (!message) return;
        await sendChatMessage(message, { echoUser: true });
      });

      loadSettingsFromStorage();
      for (const key of Object.keys(settingsEls)) {
        settingsEls[key].addEventListener("change", saveSettingsToStorage);
      }
      for (const key of ["base_url", "email"]) {
        settingsEls[key].addEventListener("blur", saveSettingsToStorage);
      }
      status("Ready. Ask Einstein a question to begin.", "info");

      appendChatBubble(
        "assistant",
        "Einstein here. Ask a question like 'who owns vault oncall?' then follow up with 'summarize result 2', 'compare #1 and #3', or 'show more'. I can also apply filters like 'in ENG and OPS spaces', 'since 2026-01-01', or 'excluding draft'."
      );
    </script>
  </body>
</html>
"""


class SearchHandler(BaseHTTPRequestHandler):
    server_version = "ConfluenceSearchWeb/1.0"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path == "/" or self.path.startswith("/?"):
            self._get_or_create_conversation_state()
            self._write_html(INDEX_HTML)
            return
        self._write_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path not in {"/api/search", "/api/chat", "/api/chat/clear"}:
            self._write_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
            return

        if self.path == "/api/chat/clear":
            self._handle_chat_clear()
            return

        payload = self._read_json_payload()
        if payload is None:
            return

        if self.path == "/api/chat":
            self._handle_chat(payload)
            return

        query = str(payload.get("query", "")).strip()
        if not query:
            self._write_json({"error": "query is required"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            agent = ConfluenceSearchAgent(
                base_url=str(payload.get("base_url", "")).strip(),
                personal_access_token=str(payload.get("personal_access_token", "")).strip()
                or None,
                email=str(payload.get("email", "")).strip() or None,
                api_token=str(payload.get("api_token", "")).strip() or None,
            )
            limit = int(payload.get("limit", 10))
            limit = max(1, min(limit, 50))
            space_key = payload.get("space_key")
            incoming_space_keys = payload.get("space_keys")
            space_keys = (
                [str(item) for item in incoming_space_keys]
                if isinstance(incoming_space_keys, list)
                else None
            )
            incoming_exclude_terms = payload.get("exclude_terms")
            exclude_terms = (
                [str(item) for item in incoming_exclude_terms]
                if isinstance(incoming_exclude_terms, list)
                else None
            )
            modified_since = str(payload.get("modified_since") or "").strip() or None
            modified_before = str(payload.get("modified_before") or "").strip() or None
            results = agent.search(
                query=query,
                limit=limit,
                space_key=space_key,
                space_keys=space_keys,
                exclude_terms=exclude_terms,
                modified_since=modified_since,
                modified_before=modified_before,
            )
            self._write_json({"results": [result.__dict__ for result in results]})
        except (ValueError, ConfluenceSearchError) as exc:
            self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception:
            self._write_json(
                {"error": "Unexpected server error while searching Confluence."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_chat(self, payload: dict[str, Any]) -> None:
        message = str(payload.get("message", "")).strip()
        if not message:
            self._write_json({"error": "message is required"}, status=HTTPStatus.BAD_REQUEST)
            return

        settings = payload.get("settings", {})
        if not isinstance(settings, dict):
            self._write_json(
                {"error": "settings must be an object"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        session_state = self._get_or_create_conversation_state()
        try:
            agent = ConfluenceConversationAgent(session_state)
            turn = agent.handle_message(message, settings)
            self._write_json(
                {
                    "reply": turn.reply,
                    "results": serialize_results(turn.results),
                }
            )
        except (ValueError, ConfluenceSearchError) as exc:
            self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception:
            self._write_json(
                {"error": "Unexpected server error while handling conversation."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_chat_clear(self) -> None:
        session_state = self._get_or_create_conversation_state()
        session_state.last_query = ""
        session_state.last_results = []
        session_state.last_page_ids = []
        session_state.previous_query = ""
        session_state.previous_results = []
        session_state.previous_page_ids = []
        self._write_json({"reply": "Einstein: Conversation cleared."})

    def _read_json_payload(self) -> dict[str, Any] | None:
        try:
            raw_length = self.headers.get("Content-Length", "0")
            length = int(raw_length)
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            payload = json.loads(raw or "{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object.")
            return payload
        except (ValueError, json.JSONDecodeError) as exc:
            self._write_json(
                {"error": f"Invalid JSON payload: {exc}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return None

    def _write_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self._maybe_set_session_cookie()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._maybe_set_session_cookie()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_or_create_conversation_state(self) -> ConversationState:
        self._purge_expired_sessions()
        cookie_header = self.headers.get("Cookie", "")
        cookies = SimpleCookie()
        if cookie_header:
            cookies.load(cookie_header)

        session_id = cookies[SESSION_COOKIE].value if SESSION_COOKIE in cookies else ""
        if session_id and session_id in SESSION_STATES:
            entry = SESSION_STATES[session_id]
            entry["last_seen"] = int(time.time())
            return entry["state"]

        session_id = secrets.token_urlsafe(24)
        state = ConversationState()
        SESSION_STATES[session_id] = {
            "state": state,
            "created_at": int(time.time()),
            "last_seen": int(time.time()),
        }
        self._session_id_to_set = session_id
        return state

    @staticmethod
    def _purge_expired_sessions() -> None:
        now = int(time.time())
        expired = [
            sid
            for sid, entry in SESSION_STATES.items()
            if now - int(entry.get("last_seen", 0)) > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            SESSION_STATES.pop(sid, None)

    def _maybe_set_session_cookie(self) -> None:
        session_id = getattr(self, "_session_id_to_set", None)
        if not session_id:
            return
        cookie = (
            f"{SESSION_COOKIE}={session_id}; Max-Age={SESSION_TTL_SECONDS}; "
            "Path=/; HttpOnly; SameSite=Lax"
        )
        self.send_header("Set-Cookie", cookie)
        self._session_id_to_set = None

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep logs concise for local usage.
        print(f"[{self.address_string()}] {fmt % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local Confluence search web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), SearchHandler)
    print(f"Confluence search UI running on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
