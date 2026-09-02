# Submission notes

## Core decision

The primary metric compares a specified contributor with Stack Exchange's official all-time Top-20
answerers for the tag during a caller-selected half-open period of at most 31 days. If necessary,
the subject is added as a twenty-first member. This produces an honest, bounded benchmark rank; it
does not claim to discover the global Top-20 for an arbitrary historical interval.

The population choice avoids the quota-prohibitive operation of crawling every question and answer
to discover an arbitrary-period global leaderboard. Native Stack Overflow scores remain the
ranking fact—there is no invented weighted contribution score.

## Reliability

At most six combined user-answer pages are processed per period. Parent questions are fetched in
100-ID batches to verify the requested tag and build topic fingerprints. Current and previous
periods therefore have a hard upper bound of 25 upstream calls including an uncached cohort lookup.
Budget overflow fails explicitly rather than returning incomplete analytics.

SQLite stores the official cohort snapshot, stable upstream entities, and transactional page
checkpoints. Synchronization is idempotent and resumable. The HTTP adapter handles quota, provider
backoff, pagination, batching, timeout, partial failure, and bounded transient retry.

The optional OpenAI Responses API integration receives only deterministic analysis and a closed
evidence set. Pydantic Structured Outputs define its schema, and application validation rejects
unknown evidence IDs.

## Run locally

The reviewer path is `docker compose up --build`, followed by opening `http://127.0.0.1:8000`.
Docker contains Python, `uv`, and the application; the browser UI accepts a profile URL and renders
the analytics without curl or jq. Stack Overflow analytics require no credentials. Put
`SOA_OPENAI_API_KEY` in a private `.env` only when testing the optional narrative. Native Python
3.12+ and `uv` execution remains available for development.

## With another day

I would add an explicit cohort-refresh operation, conditional narrative caching keyed by analysis
hash/model/prompt version, Alembic migrations, OpenTelemetry metrics, browser-level UI tests, and an
eval fixture set for narrative faithfulness. For a larger deployment I would move to Postgres and a
worker while preserving the existing ports.

## AI usage

I used an AI coding assistant to help scaffold modules, enumerate failure cases, and review tests
and documentation. I verified API constraints, selected and documented the benchmark population,
kept every metric deterministic, and ran formatting, linting, and the complete offline suite.
