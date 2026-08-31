# Submission notes

## Run locally

Install Python 3.12+ and `uv`, copy `.env.example` to `.env`, optionally set
`SOA_OPENAI_API_KEY` for LLM narratives, then run `uv sync --frozen` and
`uv run uvicorn stack_overflow_analyzer.main:app --host 127.0.0.1 --port 8000`. The deterministic endpoints need no
credentials, including no Stack Overflow key or login. `docker compose up --build` is the
one-command alternative.

## Architecture and decisions

FastAPI calls small application services through abstract Stack Exchange, repository, and narrative
boundaries. The async HTTP adapter handles Stack Exchange's pagination, batching, backoff, quota,
and retry contract; the SQLAlchemy adapter gives SQLite idempotent upserts and transactional page
checkpoints. Pure analytics owns ranking, medians, comparisons, date arithmetic, topic fingerprints,
and evidence creation. The OpenAI adapter uses Responses API Structured Outputs through Pydantic,
while an application-level allowlist prevents fabricated evidence references.

The period-cohort metric is intentionally narrower than “all answers to this tag posted in the
period.” Stack Exchange offers no efficient arbitrary-range endpoint for that broader query, so the
service reports a complete bounded cohort rather than silently returning incomplete data or burning
the daily quota on a site-wide answer crawl. The official all-time Top-20 remains available as a
separate upstream feature.

## With another day

I would add a small React timeline/leaderboard, background refresh with a cross-process lease,
conditional narrative caching keyed by analysis hash/model/prompt version, migrations via Alembic,
OpenTelemetry metrics, and an eval fixture set that scores narrative faithfulness and evidence
coverage before prompt/model changes. For larger deployments I would move to Postgres and a worker,
while keeping the existing ports.

## AI usage

I used an AI coding assistant to help scaffold modules, enumerate failure cases, and review tests and
documentation. I verified the API constraints, chose and documented the metric myself, kept all
analytics deterministic, and ran formatting, linting, and the full offline suite before submission.
