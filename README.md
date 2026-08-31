# Stack Overflow Contributor Analyzer

A FastAPI service that ranks contributors to a Stack Overflow technology tag over an arbitrary
date range, then asks an OpenAI model to explain the deterministic result. Python establishes every
fact; the LLM receives a closed set of evidence and may only synthesize it.

## 60-second quickstart

Prerequisites: Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
cp .env.example .env                 # PowerShell: Copy-Item .env.example .env
# Add SOA_OPENAI_API_KEY to .env only if you want the narrative endpoint.
uv sync --frozen
uv run uvicorn stack_overflow_analyzer.main:app --reload
```

Open <http://localhost:8000/docs> or try:

```bash
curl "http://localhost:8000/v1/tags/tensorflow/contributors?from_date=2025-01-01&to_date=2025-01-07&limit=20"

curl "http://localhost:8000/v1/tags/tensorflow/contributors/USER_ID?from_date=2025-01-01&to_date=2025-01-07"

curl -X POST "http://localhost:8000/v1/tags/tensorflow/contributors/USER_ID/narrative" \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2025-01-01","end_date":"2025-01-07"}'

curl "http://localhost:8000/v1/tags/tensorflow/top-answerers/all-time"
```

The first period query synchronizes the requested date range into SQLite. Contributor detail also
synchronizes the immediately preceding equivalent-length period for comparison. Repeating a query
uses its completed checkpoint without spending Stack Exchange quota. An optional Stack Apps key in
`SOA_STACK_EXCHANGE_KEY` raises the upstream daily quota.

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
4xx and exhausted quota without retrying.

The narrative adapter uses the OpenAI Responses API `responses.parse` helper with a Pydantic
Structured Output. It sends the deterministic analysis and predefined evidence objects. The
application rejects any returned evidence ID outside that set. `SOA_OPENAI_API_KEY` and
`SOA_STACK_EXCHANGE_KEY` are Pydantic `SecretStr` values and are never logged.

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

- `GET /v1/tags/{tag}/contributors` — period leaderboard; `limit` is 1–100.
- `GET /v1/tags/{tag}/contributors/{user_id}` — facts, peer/prior comparison, topics, evidence.
- `POST /v1/tags/{tag}/contributors/{user_id}/narrative` — paid external computation, hence POST.
- `POST /v1/sync` — explicit idempotent warm-up/resume operation.
- `GET /v1/tags/{tag}/top-answerers/all-time` — official Stack Exchange leaderboard.
- `GET /health` — liveness and process health.

Expected failures use 404 (no qualifying contributor), 422 (input), 429 (quota), or 502 (upstream /
narrative failure). A caller-supplied or generated `X-Request-ID` is returned on every response.

See [NOTES.md](NOTES.md) for submission trade-offs and next steps.
