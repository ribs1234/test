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

### Accuracy tuning: candidate expansion + reranking

Search now fetches a larger candidate pool first, then reranks to improve top
result quality.

Configuration:

- `CONFLUENCE_CANDIDATE_POOL_MULTIPLIER` (default: `4`)
- `CONFLUENCE_CANDIDATE_POOL_CAP` (default: `50`)

Equivalent CLI flags:

- `--candidate-pool-multiplier`
- `--candidate-pool-cap`

Example:

```bash
python3 confluence_search_agent.py "where can I find 4x9 status" \
  --limit 5 \
  --candidate-pool-multiplier 5 \
  --candidate-pool-cap 80
```

JSON output:

```bash
python confluence_search_agent.py "incident runbook" --json
```

You can also pass credentials and URL as flags (`--base-url`, `--personal-access-token`, `--email`, `--api-token`, `--bearer-token`), but environment variables are simpler for local usage.

### Optional model-backed summarizer

By default, summaries use the built-in heuristic summarizer. You can enable a
model-backed summarizer (OpenAI-compatible chat completions API) for richer
summaries, with automatic fallback to the heuristic summarizer if the model is
not configured or the model call fails.

Environment variables:

- `CONFLUENCE_SUMMARIZER_BACKEND` (`auto`, `heuristic`, or `model`; default `auto`)
- `CONFLUENCE_SUMMARIZER_API_KEY`
- `CONFLUENCE_SUMMARIZER_MODEL`
- `CONFLUENCE_SUMMARIZER_API_BASE` (optional, defaults to `https://api.openai.com/v1`)
- `CONFLUENCE_SUMMARIZER_MAX_RESULTS` (optional, defaults to `5`, max `50`)

Equivalent CLI flags:

- `--summarizer-backend`
- `--summarizer-api-key`
- `--summarizer-model`
- `--summarizer-api-base`
- `--summarizer-max-results`

Example:

```bash
export CONFLUENCE_SUMMARIZER_API_KEY="..."
export CONFLUENCE_SUMMARIZER_MODEL="gpt-4.1-mini"
python3 confluence_search_agent.py "where can I find the 4x9 status page?"
```

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
- If your question is broad/ambiguous, Einstein can ask a targeted follow-up
  clarification before running a final search.
- Follow up with:
  - `summarize result 2` (Einstein fetches that page's content for a more detailed natural summary)
  - `compare result 1 and 3`
  - `what changed result 2` (compares with the prior result set snapshot)
  - `show more`
  - `again`
  - `in ENG space ...`, `in ENG and OPS spaces ...`, or `clear space filter`
  - `top 5 ...`, `show 12 results ...`, `first 3 ...` to set result count from the question
  - `since 2026-01-01 ...`, `between 2026-01-01 and 2026-03-01 ...`, `last 14 days ...`
  - `excluding draft ...`, `without legacy ...`, or `-deprecated`
  - Clarification replies such as:
    - `1` / `2` / `3` (pick one suggested interpretation)
    - `first` / `second`
    - freeform refinement (example: `ownership in ENG space`)

Einstein infers both space filters and result count from the chat message, so
there are no dedicated **Space Key** or **Limit** UI fields.

Einstein responses are now source-grounded:
- Direct answers include inline citations (`[1]`, `[2]`, ...)
- A **Sources** section maps each citation to a concrete Confluence page link
- Responses are sectioned for readability (Direct answer, Top matches, Sources, Next actions)

The results panel also includes per-result quick actions:
- **Explain** -> sends `summarize result X`
- **Compare** -> sends `compare result X and result Y`
- **What changed** -> compares result `X` with the previous search snapshot

- Conversation controls:
  - **Clear Conversation** resets the in-session chat state and prior results
  - **Export Transcript** downloads the full chat transcript as a `.txt` file

Conversation state is stored per browser session (via local server session cookie) and used only for this local app runtime.