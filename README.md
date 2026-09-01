# Stack Overflow Contributor Analyzer

A FastAPI service that ranks contributors to a Stack Overflow technology tag over an arbitrary
date range, then asks an OpenAI model to explain the deterministic result. Python establishes every
fact; the LLM receives a closed set of evidence and may only synthesize it.

## 60-second quickstart

Prerequisites: Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

On WSL/Linux, install `uv` with Astral's official standalone installer (do not use the Snap command):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv --version
```

If the `source` file is unavailable, restart the WSL shell and run `uv --version` again. Then return
to the project directory:

```bash
cd /mnt/c/Users/nicol/stack_overflow_analyzer
```

Stack Overflow analytics require no credentials. An OpenAI API key is required only for the
`/narrative` endpoint.

### Configure the OpenAI key (optional)

Create a key in the [OpenAI API dashboard](https://platform.openai.com/api-keys). Then create your
private `.env` file—do **not** put the key in `.env.example`.

PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set the first line of `.env` to your actual key:

```dotenv
SOA_OPENAI_API_KEY=your-key-here
```

The `.env` file is already excluded by `.gitignore`. For a temporary PowerShell session instead of
a file, use:

```powershell
$env:SOA_OPENAI_API_KEY = "your-key-here"
```

macOS/Linux equivalents:

```bash
cp .env.example .env
# Edit .env and set SOA_OPENAI_API_KEY=your-key-here
# Or, for this terminal session only:
export SOA_OPENAI_API_KEY="your-key-here"
```

Now install and run:

```bash
uv sync --frozen
uv run uvicorn stack_overflow_analyzer.main:app --host 127.0.0.1 --port 8000
```

If you only need deterministic analytics, skip the key setup entirely. `docker compose` reads the
same local `.env` file automatically.

On WSL under `/mnt/c`, avoid Uvicorn's `--reload` option: its file watcher may encounter
Windows-owned cache directories and fail with `Permission denied`. The command above runs without
the watcher. If hot reload is needed, move the repository into the WSL Linux filesystem first.

Open <http://localhost:8000/docs> or try:

```bash
curl "http://localhost:8000/v1/tags/tensorflow/contributors?from_date=2025-01-01&to_date=2025-01-07&limit=20"

curl "http://localhost:8000/v1/tags/tensorflow/contributors/USER_ID?from_date=2025-01-01&to_date=2025-01-07"

curl -X POST "http://localhost:8000/v1/tags/tensorflow/contributors/USER_ID/narrative" \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2025-01-01","end_date":"2025-01-07"}'

curl "http://localhost:8000/v1/tags/tensorflow/top-answerers/all-time"
```

**Stack Overflow data is public and requires no key, token, account, or OAuth flow.** The first
period query synchronizes the requested date range into SQLite. Contributor detail also
synchronizes the immediately preceding equivalent-length period for comparison. Repeating a query
uses its completed checkpoint without spending additional Stack Exchange quota. The public API's
shared IP quota and returned `backoff` instructions are handled automatically.

Docker is also supported:

```bash
docker compose up --build
```

## What the custom metric means

`period_cohort_answer_score` ranks users by the sum of native Stack Overflow scores on answers
created during the inclusive UTC date range, limited to questions that were also created during
that range and carry the requested tag. Ties are resolved by accepted-answer count, answer count,
then user ID.

The cohort constraint is deliberate. The public Stack Exchange API can filter questions by tag and
creation time, but it cannot directly request every historical answer for a tag and arbitrary answer
creation interval. Crawling all site-wide answers would be quota-prohibitive. The chosen metric is
therefore complete and reproducible from bounded API calls; it does **not** claim to count answers
posted during the range on older questions. No weighted or LLM-derived contribution score exists.

To prevent one request from exhausting the shared public quota, every API date range is limited to
31 inclusive days by default. Longer analyses must be split into monthly requests. The guardrail is
configurable with `SOA_MAX_PERIOD_DAYS`, but increasing it is not recommended for anonymous access.

Acceptance rate is accepted answers / qualifying answers. Average score is total answer score /
qualifying answers. “Top 20” in contributor analysis refers to this custom period leaderboard; the
separate all-time endpoint exposes Stack Exchange's official tag Top-20. Peer medians use the custom
period's first 20 contributors, including the subject when applicable. The previous period has the
same number of inclusive calendar days and ends the day before the requested period begins.

## Architecture

```text
FastAPI routes and error semantics
  -> application sync / analytics / narrative services
    -> ABC ports
      -> async Stack Exchange + OpenAI adapters
      -> SQLite / SQLAlchemy repository
```

Upstream pages are the commit unit. A transaction upserts users, questions, tags, and answers using
stable Stack Exchange IDs and advances the checkpoint only after the entire question page and all
of its answer pages have succeeded. A crash leaves the page unadvanced, so retrying is safe. The
client batches question IDs, follows pagination, honors provider `backoff`, records quota, retries
only network/5xx failures with bounded exponential backoff and jitter, and rejects deterministic
4xx and exhausted quota without retrying. Anonymous access is capped at page 25, so long queries
checkpoint the last creation timestamp and continue from page 1; inclusive overlap is harmless
because stable-ID upserts remove duplicates.

The narrative adapter uses the OpenAI Responses API `responses.parse` helper with a Pydantic
Structured Output. It sends the deterministic analysis and predefined evidence objects. The
application rejects any returned evidence ID outside that set. `SOA_OPENAI_API_KEY` is a Pydantic
`SecretStr` and is never logged. It is needed only for the optional narrative endpoint.

## Development

All tests are offline; Stack Exchange, OpenAI, and HTTP boundaries are mocked.

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pre-commit run --all-files
```

Configuration is read from `.env` with the `SOA_` prefix. See [.env.example](.env.example). SQLite
is the only service dependency. Structured JSON logs include request/sync/provider context, duration,
retry, pagination, quota, and error type without request bodies or secrets.

## API semantics

- `GET /v1/tags/{tag}/contributors` — period leaderboard; `limit` is 1–100 and the period is at most 31 inclusive days.
- `GET /v1/tags/{tag}/contributors/{user_id}` — facts, peer/prior comparison, topics, evidence.
- `POST /v1/tags/{tag}/contributors/{user_id}/narrative` — paid external computation, hence POST.
- `POST /v1/sync` — explicit idempotent warm-up/resume operation.
- `GET /v1/tags/{tag}/top-answerers/all-time` — official Stack Exchange leaderboard.
- `GET /health` — liveness and process health.

Expected failures use 404 (no qualifying contributor), 422 (input or a period over 31 days), 429 (quota), or 502 (upstream /
narrative failure). A caller-supplied or generated `X-Request-ID` is returned on every response.

See [NOTES.md](NOTES.md) for submission trade-offs and next steps.
