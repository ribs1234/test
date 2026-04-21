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
  - Email + API token, or
  - Bearer token

The app sends requests from your local server process to Confluence over HTTPS.