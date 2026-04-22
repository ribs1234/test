# Lightweight Confluence Search Agent

Small Python tools for searching Confluence pages by query using the Confluence REST API.

## Requirements

- Python 3.9+
- Confluence Cloud URL and auth credentials

## Files

- `confluence_search_agent.py` - main CLI and lightweight search client
- `confluence_search_web.py` - local web UI served on `localhost`
- `confluence_conversation_agent.py` - multi-turn conversation layer for follow-up questions

## Authentication

Use one of the following:

1. **Personal Access Token (PAT)** (recommended):
   - `CONFLUENCE_PAT`
2. **Email + API token**:
   - `CONFLUENCE_EMAIL`
   - `CONFLUENCE_API_TOKEN`
3. **Bearer token**:
   - `CONFLUENCE_BEARER_TOKEN`

Always set:

- `CONFLUENCE_BASE_URL` (example: `https://your-domain.atlassian.net`)

## Usage

Basic:

```bash
python confluence_search_agent.py "incident runbook"
```

Question-style queries are supported too (for example: `"where is the vault onboarding runbook?"`).
Intent-specific boosts are applied for questions like:
- ownership (`who owns ...`) -> boosts owner/contact/team/oncall style pages
- how-to (`how do I ...`) -> boosts runbook/guide/procedure/troubleshooting content
- location (`where is ...`) -> boosts index/home/reference pages
- definition (`what is ...`) -> boosts overview/introduction/architecture pages

Filter by space and limit results:

```bash
python confluence_search_agent.py "incident runbook" --space-key ENG --limit 5
```

JSON output:

```bash
python confluence_search_agent.py "incident runbook" --json
```

You can also pass credentials and URL as flags (`--base-url`, `--personal-access-token`, `--email`, `--api-token`, `--bearer-token`), but environment variables are simpler for local usage.

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
  - **Personal Access Token (PAT)**, or
  - Email + API token, or
  - Bearer token

The app sends requests from your local server process to Confluence over HTTPS.

Search results include an AI-style summary generated from page content (no search snippet output).

### Conversation Layer (in web UI)

The web app is chatbot-first and uses **Einstein** as the single search input.
Ask your query directly in the Einstein chat box; there is no separate query
submission field.

- Ask a natural question (example: `who owns vault oncall?`)
- Follow up with:
  - `summarize result 2`
  - `compare result 1 and 3`
  - `show more`
  - `again`
  - `in ENG space ...` or `clear space filter`

- Conversation controls:
  - **Clear Conversation** resets the in-session chat state and prior results
  - **Export Transcript** downloads the full chat transcript as a `.txt` file

Conversation state is stored per browser session (via local server session cookie) and used only for this local app runtime.