#!/usr/bin/env python3
"""Local web UI for searching Confluence pages."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from confluence_search_agent import ConfluenceSearchAgent, ConfluenceSearchError

SESSION_COOKIE = "confluence_session"
SESSION_TTL_SECONDS = 60 * 60 * 8
SESSIONS: dict[str, dict[str, Any]] = {}
OKTA_CLIENT_ID = os.getenv("OKTA_CLIENT_ID", "").strip()

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
      Enter your Confluence web address and search query. If your org uses Okta,
      sign in once and then search with the retrieved bearer token.
    </p>

    <details class="full" open>
      <summary><strong>Okta sign-in (recommended)</strong></summary>
      <form id="okta-form">
        <label class="full">
          Okta Issuer URL
          <input id="okta-issuer" name="okta_issuer" placeholder="https://your-org.okta.com/oauth2/default" />
        </label>
        <label class="full">
          Okta Username
          <input id="okta-username" name="okta_username" placeholder="you@company.com" />
        </label>
        <label class="full">
          Scopes
          <input id="okta-scopes" name="okta_scopes" value="openid profile email" />
        </label>
        <button type="button" id="okta-login-btn" class="full">Sign in with Okta</button>
      </form>
      <div id="okta-status" class="hint"></div>
    </details>

    <form id="search-form">
      <label class="full">
        Confluence Base URL
        <input id="base-url" name="base_url" placeholder="https://your-domain.atlassian.net" required />
      </label>

      <label>
        Email (manual API token auth)
        <input id="email" name="email" placeholder="you@company.com" />
      </label>
      <label>
        API Token (manual API token auth)
        <input id="api-token" name="api_token" type="password" />
      </label>

      <label class="full">
        Bearer Token (optional manual override)
        <input id="bearer-token" name="bearer_token" type="password" />
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
      const oktaForm = document.getElementById("okta-form");
      const oktaStatusEl = document.getElementById("okta-status");
      const oktaLoginBtn = document.getElementById("okta-login-btn");
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

      function loadOktaConfig() {
        const issuer = localStorage.getItem("okta_issuer") || "";
        const username = localStorage.getItem("okta_username") || "";
        const scopes = localStorage.getItem("okta_scopes") || "openid profile email";
        document.getElementById("okta-issuer").value = issuer;
        document.getElementById("okta-username").value = username;
        document.getElementById("okta-scopes").value = scopes;
      }

      function saveOktaConfig() {
        localStorage.setItem("okta_issuer", document.getElementById("okta-issuer").value.trim());
        localStorage.setItem("okta_username", document.getElementById("okta-username").value.trim());
        localStorage.setItem("okta_scopes", document.getElementById("okta-scopes").value.trim());
      }

      async function refreshAuthStatus() {
        try {
          const response = await fetch("/api/auth-status");
          const data = await response.json();
          if (data.okta_authenticated) {
            oktaStatusEl.textContent = "Okta session is authenticated.";
            oktaStatusEl.style.color = "";
          } else {
            oktaStatusEl.textContent = "Not signed in with Okta.";
            oktaStatusEl.style.color = "";
          }
        } catch (err) {
          oktaStatusEl.textContent = "Could not check Okta auth status.";
          oktaStatusEl.style.color = "crimson";
        }
      }

      oktaLoginBtn.addEventListener("click", async () => {
        saveOktaConfig();
        const payload = {
          issuer: document.getElementById("okta-issuer").value.trim(),
          username: document.getElementById("okta-username").value.trim(),
          scopes: document.getElementById("okta-scopes").value.trim(),
        };

        if (!payload.issuer || !payload.username) {
          oktaStatusEl.textContent = "Okta issuer and username are required.";
          oktaStatusEl.style.color = "crimson";
          return;
        }

        oktaStatusEl.textContent = "Redirecting to Okta...";
        oktaStatusEl.style.color = "";
        try {
          const response = await fetch("/api/okta/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data.error || "Could not start Okta login.");
          }
          window.location.href = data.redirect_url;
        } catch (err) {
          oktaStatusEl.textContent = err.message || "Could not start Okta login.";
          oktaStatusEl.style.color = "crimson";
        }
      });

      function showCallbackMessage() {
        const url = new URL(window.location.href);
        const okta = url.searchParams.get("okta");
        if (okta === "success") {
          oktaStatusEl.textContent = "Okta sign-in complete.";
          oktaStatusEl.style.color = "";
          window.history.replaceState({}, "", "/");
        } else if (okta === "error") {
          const msg = url.searchParams.get("message") || "Okta sign-in failed.";
          oktaStatusEl.textContent = msg;
          oktaStatusEl.style.color = "crimson";
          window.history.replaceState({}, "", "/");
        }
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        status("Searching...");
        resultsEl.textContent = "";

        const payload = {
          base_url: document.getElementById("base-url").value.trim(),
          email: document.getElementById("email").value.trim(),
          api_token: document.getElementById("api-token").value,
          bearer_token: document.getElementById("bearer-token").value,
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

      loadOktaConfig();
      showCallbackMessage();
      refreshAuthStatus();
    </script>
  </body>
</html>
"""


class SearchHandler(BaseHTTPRequestHandler):
    server_version = "ConfluenceSearchWeb/1.0"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._write_html(INDEX_HTML)
            return
        if parsed.path == "/auth/okta/callback":
            self._handle_okta_callback(parsed)
            return
        if parsed.path == "/api/auth-status":
            session = self._get_or_create_session()
            self._write_json(
                {"okta_authenticated": bool(session.get("okta_access_token"))}
            )
            return
        self._write_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        session = self._get_or_create_session()
        if self.path != "/api/search":
            if self.path == "/api/okta/start":
                self._handle_okta_start(session)
                return
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
                email=str(payload.get("email", "")).strip() or None,
                api_token=str(payload.get("api_token", "")).strip() or None,
                bearer_token=(
                    str(payload.get("bearer_token", "")).strip()
                    or session.get("okta_access_token")
                    or None
                ),
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

    def _handle_okta_start(self, session: dict[str, Any]) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return

        issuer = str(payload.get("issuer", "")).strip().rstrip("/")
        username = str(payload.get("username", "")).strip()
        scopes = str(payload.get("scopes", "")).strip() or "openid profile email"
        client_id = OKTA_CLIENT_ID
        if not client_id:
            self._write_json(
                {
                    "error": (
                        "Server is missing OKTA_CLIENT_ID. "
                        "Set OKTA_CLIENT_ID before starting the app."
                    )
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if not issuer or not username:
            self._write_json(
                {"error": "issuer and username are required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        verifier = self._new_pkce_verifier()
        challenge = self._pkce_challenge(verifier)
        state = secrets.token_urlsafe(24)
        redirect_uri = self._build_callback_uri()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": scopes,
            "login_hint": username,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        authorize_url = f"{issuer}/v1/authorize?{urlencode(params)}"
        session["okta_pending"] = {
            "issuer": issuer,
            "client_id": client_id,
            "username": username,
            "scopes": scopes,
            "state": state,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "created_at": int(time.time()),
        }
        session.pop("okta_access_token", None)
        session.pop("okta_id_token", None)
        self._write_json({"redirect_url": authorize_url})

    def _handle_okta_callback(self, parsed: Any) -> None:
        session = self._get_or_create_session()
        params = parse_qs(parsed.query)
        state = (params.get("state") or [""])[0]
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]
        error_desc = (params.get("error_description") or [""])[0]

        if error:
            msg = error_desc or error
            self._redirect_with_status(okta="error", message=f"Okta error: {msg}")
            return

        pending = session.get("okta_pending")
        if not pending:
            self._redirect_with_status(okta="error", message="Missing Okta login state.")
            return
        if not code or state != pending.get("state"):
            self._redirect_with_status(okta="error", message="Invalid Okta callback state.")
            return
        if int(time.time()) - int(pending.get("created_at", 0)) > 600:
            self._redirect_with_status(okta="error", message="Okta login expired. Try again.")
            return

        try:
            token_data = self._exchange_okta_code(
                issuer=pending["issuer"],
                client_id=pending["client_id"],
                code=code,
                verifier=pending["code_verifier"],
                redirect_uri=pending["redirect_uri"],
            )
        except ConfluenceSearchError as exc:
            self._redirect_with_status(okta="error", message=str(exc))
            return

        session["okta_access_token"] = token_data.get("access_token")
        session["okta_id_token"] = token_data.get("id_token")
        session.pop("okta_pending", None)
        self._redirect_with_status(okta="success")

    def _exchange_okta_code(
        self,
        *,
        issuer: str,
        client_id: str,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }
        ).encode("utf-8")
        req = Request(
            f"{issuer}/v1/token",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ConfluenceSearchError(
                "Failed to exchange Okta authorization code for token."
            ) from exc

        token = payload.get("access_token")
        if not token:
            raise ConfluenceSearchError("Okta token response did not include access_token.")
        return payload

    @staticmethod
    def _new_pkce_verifier() -> str:
        # RFC 7636 allows 43-128 chars. token_urlsafe(64) yields a safe verifier.
        return secrets.token_urlsafe(64)

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _build_callback_uri(self) -> str:
        host = self.headers.get("Host", "")
        if not host:
            host = f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}/auth/okta/callback"

    def _redirect_with_status(self, *, okta: str, message: str = "") -> None:
        params: dict[str, str] = {"okta": okta}
        if message:
            params["message"] = message
        location = f"/?{urlencode(params)}"
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def _get_or_create_session(self) -> dict[str, Any]:
        self._purge_expired_sessions()
        cookie_header = self.headers.get("Cookie", "")
        cookies = SimpleCookie()
        if cookie_header:
            cookies.load(cookie_header)

        session_id = None
        if SESSION_COOKIE in cookies:
            session_id = cookies[SESSION_COOKIE].value

        if session_id and session_id in SESSIONS:
            session = SESSIONS[session_id]
            session["last_seen"] = int(time.time())
            return session

        session_id = secrets.token_urlsafe(24)
        session = {"created_at": int(time.time()), "last_seen": int(time.time())}
        SESSIONS[session_id] = session
        self._session_id_to_set = session_id
        return session

    @staticmethod
    def _purge_expired_sessions() -> None:
        now = int(time.time())
        expired_ids = [
            session_id
            for session_id, session in SESSIONS.items()
            if now - int(session.get("last_seen", 0)) > SESSION_TTL_SECONDS
        ]
        for session_id in expired_ids:
            SESSIONS.pop(session_id, None)

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

    def _maybe_set_session_cookie(self) -> None:
        session_id = getattr(self, "_session_id_to_set", None)
        if not session_id:
            return
        max_age = str(SESSION_TTL_SECONDS)
        cookie = (
            f"{SESSION_COOKIE}={session_id}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Lax"
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
