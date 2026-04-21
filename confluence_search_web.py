#!/usr/bin/env python3
"""Local web UI for searching Confluence pages."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from confluence_search_agent import ConfluenceSearchAgent, ConfluenceSearchError

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
        --surface: #ffffff;
        --surface-soft: #f5f8ff;
        --text: #0e1735;
        --muted: #4a5680;
        --border: #d7dff4;
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
      .panel {
        background: var(--surface);
        border-radius: 18px;
        border: 1px solid rgba(255, 255, 255, 0.55);
        box-shadow: var(--shadow);
        padding: 1.15rem 1.15rem 1.25rem;
        margin-bottom: 1rem;
      }
      form {
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
        min-height: 1.3rem;
        margin: 0.4rem 0 0;
        font-weight: 600;
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
      .result {
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 1rem;
        background: white;
        box-shadow: 0 4px 14px rgba(7, 23, 64, 0.06);
      }
      .result h3 {
        margin: 0;
        font-size: 1.05rem;
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
      @media (max-width: 760px) {
        form {
          grid-template-columns: 1fr;
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
        <h1>Confluence Intelligence Search</h1>
        <p>
          Search your Confluence knowledge base and get concise AI-generated summaries
          of each matching page.
        </p>
      </header>

      <section class="panel">
        <form id="search-form">
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

          <label class="full">
            Search Query
            <input id="query" name="query" placeholder="vault rotation runbook" required />
          </label>

          <label>
            Space Key (optional)
            <input id="space-key" name="space_key" placeholder="ENG" />
          </label>
          <label>
            Limit
            <input id="limit" name="limit" type="number" min="1" max="50" value="10" />
          </label>

          <div class="actions full">
            <button type="submit">Search Confluence</button>
            <span class="hint">Credentials are used only for live API calls and not persisted.</span>
          </div>
        </form>
      </section>

      <section class="panel">
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
      const form = document.getElementById("search-form");
      const statusEl = document.getElementById("status");
      const loadingEl = document.getElementById("loading-indicator");
      const resultsEl = document.getElementById("results");

      function status(message, isError = false) {
        statusEl.textContent = message;
        statusEl.style.color = isError ? "crimson" : "";
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

      function renderResults(items) {
        resultsEl.textContent = "";
        if (!items.length) {
          const empty = document.createElement("p");
          empty.textContent = "No matching pages found.";
          resultsEl.appendChild(empty);
          return;
        }

        for (const item of items) {
          const box = document.createElement("section");
          box.className = "result";

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
          box.appendChild(title);

          const meta = document.createElement("div");
          meta.className = "meta";
          const bits = [];
          if (item.space_key) bits.push("Space: " + item.space_key);
          if (item.last_modified) bits.push("Last Modified: " + item.last_modified);
          meta.textContent = bits.join(" | ");
          if (bits.length > 0) box.appendChild(meta);

          if (item.summary) {
            const summary = document.createElement("p");
            summary.className = "summary";
            summary.textContent = item.summary;
            box.appendChild(summary);
          }
          resultsEl.appendChild(box);
        }
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        status("Searching...");
        setLoading(true);
        resultsEl.textContent = "";

        const payload = {
          base_url: document.getElementById("base-url").value.trim(),
          personal_access_token: document.getElementById("personal-access-token").value,
          email: document.getElementById("email").value.trim(),
          api_token: document.getElementById("api-token").value,
          query: document.getElementById("query").value.trim(),
          space_key: document.getElementById("space-key").value.trim() || null,
          limit: Number(document.getElementById("limit").value || 10),
        };

        try {
          const response = await fetch("/api/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data.error || "Search failed.");
          }
          status(`Found ${data.results.length} result(s).`);
          renderResults(data.results);
        } catch (err) {
          status(err.message || "Search failed.", true);
        } finally {
          setLoading(false);
        }
      });
    </script>
  </body>
</html>
"""


class SearchHandler(BaseHTTPRequestHandler):
    server_version = "ConfluenceSearchWeb/1.0"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path == "/" or self.path.startswith("/?"):
            self._write_html(INDEX_HTML)
            return
        self._write_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path != "/api/search":
            self._write_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
            return

        payload = self._read_json_payload()
        if payload is None:
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
            results = agent.search(query=query, limit=limit, space_key=space_key)
            self._write_json({"results": [result.__dict__ for result in results]})
        except (ValueError, ConfluenceSearchError) as exc:
            self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception:
            self._write_json(
                {"error": "Unexpected server error while searching Confluence."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

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
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
