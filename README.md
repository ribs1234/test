# Lightweight Confluence Search Agent

Small Python tools for searching Confluence pages by query using the Confluence REST API.

## Requirements

- Python 3.9+
- Confluence Cloud URL and auth credentials

## Files

- `confluence_search_agent.py` - main CLI and lightweight search client
- `confluence_search_web.py` - local web UI served on `localhost`

## Authentication

Use one of the following:

1. **Email + API token** (recommended for Atlassian Cloud):
   - `CONFLUENCE_EMAIL`
   - `CONFLUENCE_API_TOKEN`
2. **Bearer token**:
   - `CONFLUENCE_BEARER_TOKEN`

Always set:

- `CONFLUENCE_BASE_URL` (example: `https://your-domain.atlassian.net`)

## Usage

Basic:

```bash
python confluence_search_agent.py "incident runbook"
```

Filter by space and limit results:

```bash
python confluence_search_agent.py "incident runbook" --space-key ENG --limit 5
```

JSON output:

```bash
python confluence_search_agent.py "incident runbook" --json
```

You can also pass credentials and URL as flags (`--base-url`, `--email`, `--api-token`, `--bearer-token`), but environment variables are simpler for local usage.

## Local Web UI (localhost)

Run:

```bash
python3 confluence_search_web.py --host 127.0.0.1 --port 8000
```

Open in your browser:

```text
http://127.0.0.1:8000
```

In the page, enter:
- Confluence web address (example: `https://your-domain.atlassian.net`)
- Query text
- Optional space key / limit
- Auth:
  - **Okta sign-in (OAuth PKCE)**, or
  - Email + API token, or
  - Bearer token

The app sends requests from your local server process to Confluence over HTTPS.

### Okta OAuth setup

If your org uses Okta for auth, configure these environment variables before starting the web app:

```bash
export OKTA_ISSUER="https://your-okta-domain/oauth2/default"
export OKTA_CLIENT_ID="your_okta_oidc_client_id"
# Optional overrides:
# export OKTA_SCOPE="openid profile email offline_access"
# export OKTA_AUDIENCE="api://default"
```

Then run:

```bash
python3 confluence_search_web.py --host 127.0.0.1 --port 8000
```

In the UI:
1. click **Sign in with Okta**
2. complete login in Okta
3. return to app and search Confluence (token is used automatically)

Notes:
- Your Okta app must allow redirect URI: `http://127.0.0.1:8000/auth/callback`
- If your Okta policy issues refresh tokens, the app can silently refresh access tokens