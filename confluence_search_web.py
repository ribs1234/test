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
        color-scheme: light dark;
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      }
      body {
        margin: 0;
        padding: 2rem;
        max-width: 900px;
        margin-inline: auto;
      }
      h1 { margin-top: 0; }
      form {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.75rem;
        margin-bottom: 1rem;
      }
      .full { grid-column: 1 / -1; }
      label {
        display: flex;
        flex-direction: column;
        font-size: 0.9rem;
        gap: 0.25rem;
      }
      input, button {
        padding: 0.55rem 0.6rem;
        font-size: 0.95rem;
      }
      button {
        width: fit-content;
        cursor: pointer;
      }
      .hint {
        font-size: 0.85rem;
        opacity: 0.8;
      }
      #status {
        min-height: 1.25rem;
        margin: 0.5rem 0 1rem;
      }
      .result {
        border: 1px solid #8a8a8a66;
        border-radius: 8px;
        padding: 0.9rem;
        margin-bottom: 0.75rem;
      }
      .result h3 {
        margin: 0 0 0.4rem;
      }
      .meta {
        font-size: 0.82rem;
        opacity: 0.85;
        margin-bottom: 0.45rem;
      }
      code {
        padding: 0.1rem 0.3rem;
        border-radius: 4px;
        background: #8883;
      }
    </style>
  </head>
  <body>
    <h1>Confluence Search (Localhost)</h1>
    <p class="hint">
      Enter your Confluence web address and personal access token, then search by text.
    </p>

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
        Query
        <input id="query" name="query" placeholder="incident runbook" required />
      </label>

      <label>
        Space Key (optional)
        <input id="space-key" name="space_key" placeholder="ENG" />
      </label>
      <label>
        Limit
        <input id="limit" name="limit" type="number" min="1" max="50" value="10" />
      </label>

      <button type="submit" class="full">Search Confluence</button>
    </form>

    <div id="status" aria-live="polite"></div>
    <div id="results"></div>

    <script>
      const form = document.getElementById("search-form");
      const statusEl = document.getElementById("status");
      const resultsEl = document.getElementById("results");

      function status(message, isError = false) {
        statusEl.textContent = message;
        statusEl.style.color = isError ? "crimson" : "";
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

          if (item.excerpt) {
            const excerpt = document.createElement("p");
            excerpt.textContent = item.excerpt;
            box.appendChild(excerpt);
          }
          resultsEl.appendChild(box);
        }
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        status("Searching...");
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
