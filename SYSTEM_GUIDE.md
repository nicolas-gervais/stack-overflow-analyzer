# Stack Overflow Contributor Analyzer — Complete System Guide

This document explains the system as implemented. It is intended to support a code review, a live
demo, and a technical interview without requiring prior familiarity with the repository.

## 1. Executive summary

The service answers a deliberately bounded question:

> How did a specified Stack Overflow contributor perform on a technology tag during a selected
> period, compared with Stack Exchange's official all-time Top-20 answerers for that tag?

The system integrates with the public Stack Exchange API, stores fetched entities and synchronization
state in SQLite, calculates metrics deterministically in Python, exposes the results through FastAPI,
and optionally asks an OpenAI model to explain the already-calculated evidence.

The service does **not** attempt to discover the global Top-20 for an arbitrary historical period.
Doing that through the public API would require crawling the entire tag population and could exhaust
the anonymous quota. Instead, it uses a transparent benchmark population that Stack Exchange can
provide in one call: the official all-time Top-20 for the tag.

## 2. Assignment requirements and how the system satisfies them

| Assignment expectation | Implementation |
|---|---|
| Integrate with an external API | Async integration with Stack Exchange API v2.3 |
| Fetch enough data for a meaningful insight | Official tag leaders, their period answers, answer scores and acceptance state, and parent-question tags |
| HTTP insight endpoint over a period | `GET /v1/tags/{tag}/contributors/{user_id}` |
| LLM narrative endpoint | `POST /v1/tags/{tag}/contributors/{user_id}/narrative` |
| Interesting numbers | Cohort rank, volume, native score, acceptance, average score, peer means, period-over-period change, and scoped co-occurring tags |
| Evidence chain | Predefined evidence objects with IDs; returned LLM IDs are allowlist-validated |
| Metric documentation | README, this guide, and the metric definition returned by the API |
| Local execution | Docker/browser quickstart and native `uv` commands |
| Correct HTTP semantics | Typed inputs/outputs, GET for deterministic reads, POST for paid LLM work, and explicit error status codes |
| Security | No committed secrets, `SecretStr`, input validation, URL encoding, no request-body logging, parameterized SQLAlchemy queries |
| Performance | Batching, pagination, SQLite caching, idempotent upserts, and hard request/page budgets |
| Operability | Browser UI, structured logs, request IDs, health endpoint, Swagger UI, deterministic failures |
| Tests | Offline tests covering UI assets, analytics, API, persistence, synchronization, retries, quota, backoff, and LLM boundaries |
| Containerization | Non-root Docker image and Compose-managed SQLite volume |
| Submission notes | `NOTES.md` contains setup, decisions, next steps, and AI usage |

Remaining limitations and deliberately deferred features are listed under
[Known limitations](#20-known-limitations-and-deliberate-trade-offs).

## 3. Terminology

### Requested period

A caller-selected UTC date interval represented as:

```text
[start_date, end_date)
```

The start is included and the end is excluded.

### Official cohort

The official all-time Top-20 answerers returned by Stack Exchange for the requested tag.

### Subject

The contributor identified by `{user_id}` in a detail or narrative request.

### Comparison cohort

The official cohort plus the subject when the subject is not already an official member. Its size is
therefore normally 20 or 21.

### Period benchmark rank

The contributor's rank within the comparison cohort for the requested period. It is not a global
historical Stack Overflow rank.

### Qualifying answer

An answer that satisfies all of these conditions:

1. Its owner is in the comparison cohort.
2. Its creation timestamp is in the requested half-open period.
3. Its parent question contains the requested tag.

The parent question may have been created before the requested period.

## 4. Why this metric was chosen

Stack Exchange exposes an official Top-20 endpoint for either all time or the rolling last 30 days.
It does not expose an official Top-20 for arbitrary caller-selected dates.

A true arbitrary-period global leaderboard would require:

1. Finding every question carrying the tag.
2. Fetching every relevant answer.
3. Grouping all answer owners.
4. Sorting the entire contributor population.

That approach is expensive and was observed to consume the public quota too quickly. Restricting the
population to the official all-time Top-20 creates a stable and explainable benchmark. Adding the
subject preserves the primary `user + tag + period` use case even when that user is not already an
all-time leader.

The resulting insight remains useful:

> During this period, how did the subject compare with people who have historically demonstrated the
> strongest contribution to this tag?

The cohort choice introduces survivorship and historical-leader bias. The API reports the cohort
source and snapshot time so that this limitation is visible rather than hidden.

## 5. Exact deterministic metric specification

The metric name is:

```text
all_time_top20_period_benchmark
```

### 5.1 Ranking inputs

For every comparison-cohort member, Python calculates:

- `answer_count`
- `total_answer_score`
- `accepted_answer_count`
- `acceptance_rate`
- `average_answer_score`

### 5.2 Formulas

```text
answer_count = number of qualifying answers

total_answer_score = sum(answer.score for every qualifying answer)

accepted_answer_count = number of qualifying answers where is_accepted is true

acceptance_rate = accepted_answer_count / answer_count

average_answer_score = total_answer_score / answer_count
```

When `answer_count` is zero, both rates are represented as `0`. The explicit
`has_qualifying_answers` field distinguishes this case from measured zero-valued rates.

Rates and averages are rounded to four decimal places.

### 5.3 Ranking order

There is no weighted or LLM-derived score. Contributors are sorted lexicographically by:

1. `total_answer_score` descending
2. `accepted_answer_count` descending
3. `answer_count` descending
4. `user_id` ascending

The user ID provides a stable final tie-breaker, making repeated calculations deterministic.

Only contributors with at least one qualifying answer are ranked. Inactive cohort members are
returned after the ranked members with `period_benchmark_rank: null`; consequently, they cannot rank
above an active contributor whose native total score is negative.

### 5.4 Official rank versus period benchmark rank

- `official_all_time_rank` comes directly from the cached Stack Exchange cohort response.
- `period_benchmark_rank` is calculated locally from qualifying period activity.
- `is_official_all_time_top_20` says whether the contributor came from the official cohort.

If the subject is added as the twenty-first member, their official rank is `null`. They receive a
period benchmark rank only when they have at least one qualifying answer.

### 5.5 Peer comparisons

The peer group contains official all-time Top-20 members who produced at least one qualifying answer
during the selected period. The subject is excluded, even when they are an official member. This
prevents inactive zero rows and the subject's own metrics from diluting the comparison.

For answer count, total score, acceptance rate, and average score:

```text
absolute_difference = subject_value - official_cohort_mean

percent_difference = absolute_difference / abs(official_cohort_mean)
```

`percent_difference` is `null` when there are no active peers or when their mean is zero because
division by zero would not be meaningful. Means and differences are rounded to four decimal places.
The response includes `peer_count` so the caller can judge how broad the comparison is.

### 5.6 Previous-period comparison

The previous period is immediately adjacent and has exactly the same number of days:

```text
duration = end_date - start_date

previous = [start_date - duration, start_date)
```

Example:

```text
Current:  [2025-01-01, 2025-02-01)
Previous: [2024-12-01, 2025-01-01)
```

For a 28-day February interval, the previous interval is the preceding 28 days, not necessarily the
previous named calendar month. Equal duration avoids comparing raw counts from unequal windows.

Changes are calculated as:

```text
answer_count_change = current answer count - previous answer count

total_answer_score_change = current total score - previous total score

acceptance_rate_change = current acceptance rate - previous acceptance rate

period_benchmark_rank_change = previous rank - current rank
```

A positive rank change means improvement because a smaller current rank number is better.
The previous rank and rank change are `null` when either period has no qualifying subject answers,
because there is no rank to compare.

### 5.7 Topic fingerprint

The system finds tags that co-occur with the requested tag on the subject's qualifying parent
questions. It excludes the requested tag, groups by related tag, orders by answer count descending
and tag name ascending, and returns at most five tags.

```text
share_of_answers = answers on questions with related tag / subject qualifying answers
```

One answer can contribute to multiple related tags, so related-tag shares do not need to sum to 1.

## 6. Date semantics and validation

All domain timestamps are treated as UTC.

The API accepts dates, not arbitrary timestamps. A date becomes midnight UTC:

```text
2025-01-01 -> 2025-01-01T00:00:00Z
```

Validation rules are:

- `end_date` must be strictly after `start_date`.
- The half-open interval may contain at most 31 days.
- The configured maximum may be lowered but cannot exceed 31.

These examples are valid:

```text
[2025-01-01, 2025-02-01)  # 31 days; exactly January
[2025-02-01, 2025-03-01)  # 28 days
[2025-01-10, 2025-01-17)  # 7 days
```

These are invalid:

```text
[2025-01-01, 2025-01-01)  # empty
[2025-02-01, 2025-01-01)  # reversed
[2025-01-01, 2025-02-02)  # 32 days
```

The Stack Exchange API uses integer Unix timestamps. To preserve an exclusive end, the adapter sends
`todate = end_midnight_timestamp - 1`, while SQL queries use `creation_date < end_exclusive`.

## 7. High-level architecture

```text
HTTP client
    |
    v
FastAPI routes and exception mapping
    |
    +--> AnalyticsService
    |       |
    |       +--> SyncService
    |       |       +--> StackExchangeGateway (ABC)
    |       |       +--> AnalyticsRepository (ABC)
    |       |
    |       +--> AnalyticsRepository (ABC)
    |
    +--> NarrativeService
            |
            +--> AnalyticsService
            +--> NarrativeGenerator (ABC)

Concrete adapters:
    StackExchangeGateway -> async httpx StackExchangeClient
    AnalyticsRepository  -> async SQLAlchemy SQLiteAnalyticsRepository
    NarrativeGenerator   -> OpenAINarrativeGenerator
```

The boundaries follow dependency inversion:

- Application services depend on abstract ports.
- Adapters implement network and database details.
- FastAPI composes concrete adapters at startup.
- Tests inject fakes without live network access.

## 8. Repository layout

```text
src/stack_overflow_analyzer/
  api/app.py                    FastAPI composition, routes, middleware, error mapping
  api/static/index.html         Reviewer UI structure and accessible form
  api/static/styles.css         Responsive UI styling with no external assets
  api/static/app.js             URL parsing, API calls and safe result rendering
  application/analytics.py      Deterministic metric, ranking, comparisons, evidence
  application/sync.py           Cohort caching, pagination, budget, resumable sync
  application/narratives.py     LLM orchestration and evidence-ID validation
  adapters/stack_exchange.py    Async Stack Exchange HTTP client
  adapters/database.py          SQLite schema, upserts, checkpoints, aggregate queries
  adapters/openai_narrative.py  OpenAI Responses API Structured Output adapter
  ports/stack_exchange.py       Abstract upstream boundary
  ports/repository.py           Abstract persistence boundary
  ports/narrative.py            Abstract narrative boundary
  domain/models.py              Pydantic domain and response models
  domain/exceptions.py          Expected application exception hierarchy
  config.py                     Pydantic settings
  logging.py                    structlog JSON configuration
  main.py                       ASGI import target

tests/
  test_api.py                   HTTP contract and validation
  test_dates_and_analytics.py   Date logic, ranking, cohort and comparison logic
  test_repository_and_sync.py   SQLite, idempotency, budget and resumability
  test_stack_exchange_client.py Retries, backoff, quota, batching and malformed data
  test_narratives.py            Structured output and evidence validation
  fakes.py                      In-memory test doubles
```

The project uses a `src/` layout and absolute Python imports.

## 9. External Stack Exchange API integration

The default base URL is:

```text
https://api.stackexchange.com/2.3
```

The default site is:

```text
stackoverflow
```

### 9.1 Official cohort request

```http
GET /tags/{encoded_tag}/top-answerers/all_time
```

Parameters:

```text
site=stackoverflow
pagesize=20
```

The tag is URL-encoded, so a tag such as `c#` is safely represented as `c%23`.

The adapter reads at most the first 20 items and records:

- Official rank based on response order
- User ID
- Display name
- Profile URL
- Official tag score
- Official post count
- Returned quota
- Local retrieval timestamp

### 9.2 Combined cohort-answer request

```http
GET /users/{id1;id2;...}/answers
```

The system sends all official cohort IDs plus the subject when necessary. There are at most 21 IDs,
well below the Stack Exchange limit of 100 IDs.

Parameters:

```text
site=stackoverflow
fromdate=<UTC start epoch>
todate=<UTC exclusive end epoch minus one second>
sort=creation
order=asc
page=<checkpoint page>
pagesize=100
```

This endpoint returns answers across all tags for the selected users. Parent questions are required
to establish which answers belong to the requested tag.

### 9.3 Parent-question batch request

```http
GET /questions/{question_id1;question_id2;...}
```

The adapter de-duplicates IDs and accepts at most 100, matching the answer page size. The parent
response supplies the authoritative question tags used for filtering and topic fingerprints.

If an answer page is empty, no parent-question HTTP request is made.

### 9.4 Response validation

Every successful upstream response must be JSON shaped like an object with an `items` array. The
wrapper may also contain `has_more`, `quota_remaining`, `quota_max`, and `backoff`.

Pydantic validates wrapper and entity data. Missing required answer or question fields cause a
controlled upstream error. A malformed page is never committed as complete.

## 10. Pagination and hard request budget

Stack Exchange pages contain at most 100 answers. `SyncService` follows `has_more`, advancing one
page only after the current page and its parent-question data have been committed.

The hard default and maximum are six answer pages per period:

```text
SOA_STACK_EXCHANGE_MAX_ANSWER_PAGES_PER_PERIOD=6
```

The value may be lowered but Pydantic configuration validation prevents values above six.

### 10.1 Maximum upstream calls

For each non-empty answer page:

```text
1 user-answer call + 1 parent-question call = 2 calls
```

Therefore:

```text
Leaderboard, uncached cohort:
  1 cohort + (6 answer pages * 2) = at most 13 calls

Contributor detail or narrative, uncached cohort:
  1 cohort + (6 current pages * 2) + 1 subject identity + (6 previous pages * 2)
  = at most 26 calls
```

Actual usage is commonly lower because:

- The cohort is cached after its first successful retrieval.
- A final empty or partially filled page still ends pagination.
- An empty answer page does not trigger a question request.
- Completed synchronization checkpoints bypass the upstream entirely.
- A subject identity lookup is needed only when an outside-cohort user has no qualifying current
  answers and their identity is not already cached.

If page six says `has_more=true`, the page itself is committed, the synchronization is marked failed,
and the service raises a request-budget error before requesting page seven. No partial analytics are
returned. The caller must choose a shorter interval.

## 11. Retry, timeout, backoff, and quota behavior

### 11.1 Timeout

The default `httpx.AsyncClient` timeout is 15 seconds per Stack Exchange request and is configurable
through `SOA_STACK_EXCHANGE_TIMEOUT_SECONDS`.

### 11.2 Retryable failures

The client retries:

- `httpx.TransportError`, including transient connection failures
- HTTP 5xx responses

The default is three retries after the initial request, for at most four attempts.

### 11.3 Non-retryable failures

The client does not retry:

- Deterministic HTTP 4xx errors
- Stack Exchange `throttle_violation`
- Locally known exhausted quota
- Malformed JSON
- Structurally invalid JSON
- Malformed question or answer entities

Retrying these would either waste quota or repeat a deterministic failure.

### 11.4 Exponential backoff and jitter

Before a retry, the client waits:

```text
wait_seconds = 0.5 * 2^attempt + random(0, 1) * 0.25
```

With the default three retries, approximate wait ranges are:

```text
Retry 1: 0.50–0.75 seconds
Retry 2: 1.00–1.25 seconds
Retry 3: 2.00–2.25 seconds
```

The jitter reduces synchronized retry spikes when multiple clients encounter the same failure.

### 11.5 Provider-directed backoff

When Stack Exchange returns `backoff=N`, the client stores a monotonic deadline. Before the next
request it waits until that deadline. Provider backoff takes priority over making another call; it is
not treated as an ordinary retry delay.

### 11.6 Quota tracking

The client remembers the latest `quota_remaining` value. When it reaches zero, the next attempted
request raises locally without performing HTTP. A `throttle_violation` response is also translated
to `QuotaExhaustedError`.

The API maps quota exhaustion to HTTP 429 with:

```http
Retry-After: 86400
```

That value is a conservative one-day signal; the service does not calculate the provider's exact
reset timestamp.

## 12. Synchronization, idempotency, and resumability

### 12.1 Synchronization identity

User IDs are sorted and de-duplicated. Their comma-separated representation is hashed with SHA-256,
and the first 12 hexadecimal characters are used in the internal scope:

```text
benchmark:{tag}:{cohort_digest}
```

The database checkpoint is unique for:

```text
scope + start_date + end_date
```

This prevents a 20-member leaderboard sync from being confused with a 21-member subject sync.

### 12.2 In-process duplicate suppression

`SyncService` maintains `asyncio.Lock` instances keyed by cohort/tag/period. Concurrent requests in
the same process for the same synchronization wait on one another instead of performing duplicate
upstream work.

There is a separate per-tag lock for the official cohort snapshot.

These locks are process-local. Multiple Uvicorn workers would need a database lease or distributed
lock for equivalent cross-process suppression.

### 12.3 Natural commit unit

One answer page plus its parent-question batch is the natural upstream unit of work.

The order is:

1. Fetch one combined answer page.
2. Parse and validate answers.
3. Fetch de-duplicated parent question IDs.
4. Parse and validate questions.
5. Retain questions containing the requested tag.
6. Retain answers whose parent is qualifying and whose owner belongs to the cohort.
7. In one SQLite transaction, upsert entities and advance the checkpoint.

If any step before the transaction fails, the checkpoint does not advance. If the transaction fails,
all entity and checkpoint changes roll back together.

### 12.4 Resume behavior

A failed run records the exception class name and retains `next_page`. The next request for the same
scope and dates sets the run back to `running` and resumes from that page.

A completed run returns its stored result immediately and spends no upstream quota.

If an already-committed page is submitted again, the repository treats an older page number as an
idempotent no-op. A future page arriving out of sequence raises an internal error rather than
silently corrupting the checkpoint.

### 12.5 Stable-ID upserts

Upstream entities are keyed by stable Stack Exchange IDs:

- `users.user_id`
- `questions.question_id`
- `answers.answer_id`
- `(question_id, tag)` for question tags

SQLite `ON CONFLICT DO UPDATE` refreshes mutable fields. Question tags are replaced transactionally
when a question is refreshed. This makes overlapping or retried data safe.

## 13. SQLite schema

### `users`

| Column | Meaning |
|---|---|
| `user_id` | Stable Stack Exchange user ID; primary key |
| `display_name` | Latest fetched display name |
| `profile_url` | Public profile URL |
| `reputation` | Latest fetched reputation when present |

### `questions`

| Column | Meaning |
|---|---|
| `question_id` | Stable question ID; primary key |
| `creation_date` | Question creation timestamp |
| `title` | Question title |
| `link` | Public question URL |
| `owner_user_id` | Optional foreign key to `users` |

### `question_tags`

| Column | Meaning |
|---|---|
| `question_id` | Parent question ID |
| `tag` | One question tag |

The composite primary key is `(question_id, tag)`.

### `answers`

| Column | Meaning |
|---|---|
| `answer_id` | Stable answer ID; primary key |
| `question_id` | Parent question ID |
| `owner_user_id` | Answering contributor |
| `creation_date` | Answer creation timestamp |
| `score` | Native Stack Overflow answer score |
| `is_accepted` | Whether the answer was accepted when fetched |
| `link` | Public answer URL when present |

### `sync_runs`

| Column | Meaning |
|---|---|
| `sync_id` | UUID primary key |
| `tag` | Internal benchmark scope, despite the legacy column name |
| `start_date`, `end_date` | Half-open period identity |
| `status` | `running`, `completed`, or `failed` |
| `cursor_from` | UTC fetch start retained for resumability |
| `next_page` | Next answer page to request |
| `pages_completed` | Successfully committed pages |
| `questions_upserted` | Qualifying question upsert count |
| `answers_upserted` | Qualifying answer upsert count |
| `quota_remaining` | Latest provider quota seen |
| `error_type` | Last failure class name |
| `updated_at` | Last state transition |

The unique constraint is `(tag, start_date, end_date)`.

### `cohort_snapshots`

| Column | Meaning |
|---|---|
| `tag` | Requested tag; primary key |
| `retrieved_at` | Local UTC snapshot timestamp |
| `quota_remaining` | Quota reported with the cohort response |

### `cohort_members`

| Column | Meaning |
|---|---|
| `tag`, `user_id` | Composite primary key |
| `official_rank` | Official response position, unique within tag |
| `display_name` | Snapshot display name |
| `profile_url` | Snapshot public profile URL |
| `score` | Official all-time tag score from Stack Exchange |
| `post_count` | Official all-time tag post count from Stack Exchange |

Schema creation uses SQLAlchemy metadata. There is a small compatibility alteration for the
`sync_runs.cursor_from` column, but the project does not currently use a full migration framework.

## 14. End-to-end endpoint flows

### 14.1 `GET /`

Purpose: provide a zero-tool reviewer experience in a web browser.

The self-contained HTML/CSS/JavaScript interface accepts a Stack Overflow profile URL or numeric
user ID, technology tag, and calendar month. JavaScript extracts the user ID locally and converts
the selected month into an exact half-open period. It first calls the deterministic contributor
endpoint and renders that result immediately. When **Include AI narrative** is checked (the
default), it then calls the narrative endpoint and displays the returned summary.

Analytics remain visible if OpenAI is unconfigured or fails. The narrative failure appears as a
non-fatal warning rather than replacing the deterministic result. Rendering uses DOM `textContent`
instead of inserting upstream or model strings as HTML. A restrictive Content Security Policy
allows assets and API requests only from the same origin.

### 14.2 `GET /health`

Purpose: process liveness.

Flow:

1. No database query.
2. No Stack Exchange call.
3. No OpenAI call.
4. Return `{"status":"ok"}`.

This is liveness, not deep readiness; it does not prove that SQLite or external providers are
currently reachable.

### 14.3 `GET /v1/tags/{tag}/top-answerers/all-time`

Purpose: inspect the official benchmark cohort and obtain user IDs.

Flow:

1. Normalize and validate the tag.
2. Look for the tag snapshot in SQLite.
3. Return it immediately if present.
4. Otherwise fetch the official Stack Exchange cohort.
5. Store snapshot and members transactionally.
6. Return the snapshot.

The cache has no automatic TTL or refresh endpoint in this version.

### 14.4 `GET /v1/tags/{tag}/contributors`

Purpose: rank official cohort members during the selected period.

Inputs:

- `from_date`, required
- `to_date`, required and exclusive
- `limit`, optional, 1–20, default 20

Flow:

1. Validate tag and dates.
2. Load or fetch the official cohort.
3. Synchronize current-period answers for those users.
4. Query deterministic aggregates from SQLite.
5. Insert zero-activity official members.
6. Rank active official members and return inactive members as unranked.
7. Return up to `limit` rows and `total_contributors`.

This endpoint does not fetch or return previous-period comparisons.

### 14.5 `GET /v1/tags/{tag}/contributors/{user_id}`

Purpose: detailed subject insight.

Flow:

1. Validate tag, positive user ID, and dates.
2. Load or fetch the official cohort.
3. Add the subject ID if they are outside the official cohort.
4. Synchronize current-period activity for the complete comparison cohort.
5. If an outside-cohort subject has no qualifying current-period answers, load or fetch their Stack
   Overflow identity; return 404 only when the user ID does not exist.
6. Rank active contributors and return inactive contributors with a null period rank.
7. Synchronize and rank the previous equal-length period.
8. Calculate official-cohort means.
9. Calculate subject changes; rank movement is null when either period is unranked.
10. Query subject co-occurring tags only when the subject has qualifying answers.
11. Build deterministic evidence objects.
12. Return subject, peers, previous period, evidence, and the complete current cohort table.

### 14.6 `POST /v1/tags/{tag}/contributors/{user_id}/narrative`

Purpose: explicitly request paid LLM synthesis.

Body:

```json
{
  "start_date": "2025-01-01",
  "end_date": "2025-02-01"
}
```

Flow:

1. Run the same deterministic subject analysis as the GET endpoint.
2. Send the completed analysis JSON to OpenAI.
3. Parse the response into the Pydantic narrative schema.
4. Compare returned evidence IDs with the deterministic allowlist.
5. Reject unknown IDs.
6. Return both `analysis` and `narrative`.

POST is used because the request initiates paid, non-cacheable external computation even though it
does not mutate application data conceptually.

## 15. API validation, status codes, and headers

### Tag validation

Tags are trimmed, lowercased, and must match:

```regex
^[a-z0-9][a-z0-9.+#-]{0,34}$
```

This supports common tags such as `tensorflow`, `c#`, `c++`, and `asp.net`, while preventing
unbounded or path-like input.

### User ID validation

`user_id` must be a positive integer.

### HTTP status mapping

| Status | Meaning |
|---|---|
| 200 | Successful deterministic or narrative response |
| 404 | No official cohort, or the requested Stack Overflow user ID does not exist |
| 422 | Invalid tag/date/user/limit, period over 31 days, or request budget exceeded |
| 429 | Stack Exchange quota exhausted; includes `Retry-After: 86400` |
| 502 | Stack Exchange response/network failure or OpenAI narrative failure |

FastAPI also supplies its normal validation detail structure for request-shape errors.

### Request IDs

Every request uses the caller's `X-Request-ID` header when supplied; otherwise a UUID is generated.
The value is truncated to 100 characters, bound into structlog context, and returned as
`X-Request-ID` on the response.

## 16. Response model reference

### Leaderboard response

Top-level fields:

- `tag`
- `period`
- `metric`
- `cohort`
- `contributors`
- `total_contributors`

### Contributor analysis response

Top-level fields:

- `tag`
- `period`
- `metric`
- `cohort`
- `contributor`
- `peer_comparison`
- `previous_period`
- `related_tags`
- `evidence`
- `contributors`

### Contributor metric fields

- `user_id`
- `display_name`
- `profile_url`
- `period_benchmark_rank`
- `official_all_time_rank`
- `is_official_all_time_top_20`
- `has_qualifying_answers`
- `answer_count`
- `total_answer_score`
- `accepted_answer_count`
- `acceptance_rate`
- `average_answer_score`

### Narrative fields

- `notable_contribution`
- `ranking_explanation`
- `peer_comparison`
- `period_change`
- `topic_fingerprint`
- `confidence`: `low`, `medium`, or `high`
- `evidence_ids`: at least one ID

An illustrative abbreviated subject response is:

```json
{
  "tag": "tensorflow",
  "period": {
    "start_date": "2025-01-01",
    "end_date": "2025-02-01"
  },
  "metric": {
    "name": "all_time_top20_period_benchmark",
    "description": "Ranks the documented benchmark cohort, not the global period population.",
    "ranking_order": [
      "total_answer_score descending",
      "accepted_answer_count descending (tie-breaker)",
      "answer_count descending (tie-breaker)",
      "user_id ascending (stable final tie-breaker)"
    ]
  },
  "cohort": {
    "source": "Stack Exchange official all-time tag Top-20",
    "snapshot_at": "2025-02-01T12:00:00Z",
    "official_cohort_size": 20,
    "subject_added_to_cohort": false,
    "comparison_cohort_size": 20
  },
  "contributor": {
    "user_id": 123456,
    "display_name": "Example Contributor",
    "profile_url": "https://stackoverflow.com/users/123456/example",
    "period_benchmark_rank": 4,
    "official_all_time_rank": 12,
    "is_official_all_time_top_20": true,
    "has_qualifying_answers": true,
    "answer_count": 12,
    "total_answer_score": 44,
    "accepted_answer_count": 7,
    "acceptance_rate": 0.5833,
    "average_answer_score": 3.6667
  },
  "peer_comparison": {
    "peer_group": "active_official_all_time_top_20_excluding_subject",
    "peer_count": 6,
    "total_answer_score": {
      "peer_mean": 26.0,
      "absolute_difference": 18.0,
      "percent_difference": 0.6923
    }
  },
  "previous_period": {
    "period": {
      "start_date": "2024-12-01",
      "end_date": "2025-01-01"
    },
    "period_benchmark_rank": 7,
    "period_benchmark_rank_change": 3,
    "answer_count": 9,
    "answer_count_change": 3,
    "total_answer_score": 29,
    "total_answer_score_change": 15,
    "acceptance_rate": 0.5556,
    "acceptance_rate_change": 0.0277
  },
  "related_tags": [
    {
      "tag": "keras",
      "answered_question_count": 8,
      "share_of_answers": 0.6667
    }
  ],
  "evidence": [],
  "contributors": []
}
```

The example abbreviates nested peer metrics, evidence, and contributor rows; actual responses conform
to the complete Pydantic/OpenAPI schema.

## 17. OpenAI narrative design

### 17.1 Configuration and activation

Only the narrative endpoint requires:

```text
SOA_OPENAI_API_KEY
```

If no key is configured, deterministic endpoints continue to work. Calling the narrative endpoint
returns a controlled 502 explaining that the key is required.

The default model is `gpt-5-mini`, configurable with `SOA_OPENAI_MODEL`. The default OpenAI timeout
is 30 seconds.

### 17.2 Responses API and Structured Outputs

The adapter calls:

```python
client.responses.parse(
    model=model,
    instructions=instructions,
    input=deterministic_analysis_json,
    text_format=NarrativeOutput,
    store=False,
)
```

Pydantic defines the response contract. The system rejects absent or incorrectly typed parsed output.

`store=False` asks OpenAI not to store the response through the API's response-storage feature.

### 17.3 Prompt constraints

The system instructions tell the model:

- The supplied JSON is the entire source of truth.
- Do not calculate new metrics.
- Do not introduce new facts.
- Use only exact evidence IDs from the evidence array.
- Call rank a benchmark-cohort rank, never a global period rank.
- Calibrate confidence to evidence breadth and strength.

### 17.4 Evidence defense in depth

Python creates evidence objects such as:

- `cohort.snapshot_at`
- `period.benchmark_rank`
- `period.answer_count`
- `period.total_score`
- `period.acceptance_rate`
- `period.average_score`
- `peers.mean_total_score`
- `peers.mean_answer_count`
- `previous.benchmark_rank_change`
- `previous.total_score_change`
- `previous.answer_count_change`
- Dynamic `topics.{tag}` IDs

After Structured Output parsing, the application calculates:

```text
invalid_ids = returned_evidence_ids - deterministic_evidence_ids
```

Any unknown ID rejects the entire narrative. Structured Outputs enforce shape; the allowlist enforces
provenance.

The LLM is not trusted to calculate rank, averages, means, rates, changes, tag shares, or confidence
inputs. It is used only for synthesis and qualified interpretation.

## 18. Configuration reference

Configuration is read from environment variables or `.env` using the `SOA_` prefix. Names are
case-insensitive; unknown fields are ignored.

| Variable | Default | Constraint | Purpose |
|---|---:|---:|---|
| `SOA_APP_NAME` | `Stack Overflow Analyzer` | string | FastAPI title |
| `SOA_ENVIRONMENT` | `development` | string | Deployment label reserved for operational use |
| `SOA_LOG_LEVEL` | `INFO` | logging level | Log filtering |
| `SOA_DATABASE_URL` | `sqlite+aiosqlite:///./stack_overflow.db` | SQLAlchemy URL | Persistence location |
| `SOA_MAX_PERIOD_DAYS` | `31` | 1–31 | Maximum half-open period duration |
| `SOA_STACK_EXCHANGE_BASE_URL` | `https://api.stackexchange.com/2.3` | trusted deployment setting | Stack Exchange API root |
| `SOA_STACK_EXCHANGE_SITE` | `stackoverflow` | string | Stack Exchange site parameter |
| `SOA_STACK_EXCHANGE_TIMEOUT_SECONDS` | `15.0` | greater than zero | HTTP timeout |
| `SOA_STACK_EXCHANGE_MAX_RETRIES` | `3` | 0–8 | Retries after the initial attempt |
| `SOA_STACK_EXCHANGE_MAX_ANSWER_PAGES_PER_PERIOD` | `6` | 1–6 | Hard page/request budget |
| `SOA_OPENAI_API_KEY` | unset | secret | Enables narrative endpoint |
| `SOA_OPENAI_MODEL` | `gpt-5-mini` | model string | Narrative model |
| `SOA_OPENAI_TIMEOUT_SECONDS` | `30.0` | greater than zero | OpenAI timeout |

`SOA_OPENAI_API_KEY` is loaded as Pydantic `SecretStr` and is never included in application logs.

## 19. Logging and operability

Logging uses structlog with JSON rendering and UTC ISO timestamps. Context variables attach request
IDs to logs produced during an HTTP request.

Important events include:

### `request_completed`

- request ID through context
- operation `http_request`
- HTTP method
- path without query parameters
- duration in milliseconds

### `provider_request_completed`

- provider `stack_exchange`
- API path
- duration
- retry attempt
- quota remaining and maximum
- provider backoff value

### `provider_request_retry`

- provider and path
- next retry attempt
- wait duration
- error type
- known remaining quota

### `benchmark_sync_page_committed`

- sync ID
- tag
- cohort size
- page
- cursor/start timestamp
- answers examined
- qualifying answers
- quota remaining

### `benchmark_sync_failed`

- sync ID
- tag
- cohort size
- page
- exception class

### `narrative_provider_failed`

- provider `openai`
- model
- exception class

Secrets, request bodies, OpenAI inputs, and upstream response bodies are not logged.

The FastAPI lifespan initializes tables at startup and closes OpenAI, HTTP, and database resources at
shutdown.

## 20. Known limitations and deliberate trade-offs

### Not a global period Top-20

The period rank applies only to the documented benchmark population. A contributor outside the
cohort could have outperformed every benchmark member without appearing unless selected as subject.

### Cohort snapshot bias

Using current all-time leaders to analyze an old historical month introduces survivorship bias. The
snapshot timestamp is included so consumers can interpret the result correctly.

### No automatic cohort refresh

The first successful cohort response for a tag is cached indefinitely. There is currently no TTL or
refresh endpoint. This maximizes reproducibility and quota safety but can become stale.

### On-demand synchronization latency

The first request waits for upstream synchronization. There is no background worker. Cached requests
are fast, but cold requests may take longer, especially when provider backoff is returned.

### Scores are snapshots

Stack Overflow scores and acceptance state can change after synchronization. A completed checkpoint
does not automatically refetch the same period, so results represent the fetched snapshot.

### Page budget can reject active cohorts

Six pages cover at most 600 combined answers across all selected users before tag filtering. If the
cohort produces more answers across all tags in the period, the caller must shorten the interval.

### SQLite and process-local locks

SQLite is appropriate for a local assignment and single service process. High write concurrency or
multiple worker processes would benefit from Postgres and a database-backed lease.

### Minimal migration support

Tables are created automatically, with one compatibility alteration. Production evolution would use
Alembic migrations with tested upgrade and rollback paths.

### Narrative is not cached

Repeated POST requests can incur repeated OpenAI cost even when deterministic data is cached. A
production version should cache by analysis hash, model, and prompt version.

### No authentication or authorization

The service reads public Stack Overflow data and is designed for local review. It has no application
authentication, user accounts, CORS policy, or public-internet abuse controls. Bind to localhost for
local use; production exposure would require those controls.

### No separate frontend build, background worker, or eval harness

The reviewer UI is deliberately served as static HTML/CSS/JavaScript by FastAPI. It needs no Node,
npm, frontend framework, additional container, or cross-origin configuration. A background worker
and formal narrative eval harness remain deferred.

### Health is liveness only

`GET /health` does not probe SQLite, Stack Exchange, or OpenAI.

## 21. Security review

### Secrets

- No Stack Overflow credential is required.
- The OpenAI key belongs only in `.env` or the process environment.
- `.env` is ignored by Git.
- The key is a `SecretStr`.
- Logs never include configuration dumps or request bodies.

### Untrusted input

- Tags are normalized and regex-validated.
- Tags used in upstream paths are URL-encoded.
- User IDs are positive integers.
- Limits have explicit bounds.
- Dates are typed and duration-limited.
- SQLAlchemy builds parameterized queries; user input is not concatenated into SQL.

### SSRF and provider scope

Callers cannot supply an upstream URL. The base URL is deployment configuration, which is trusted in
the same way as a database URL or any infrastructure secret.

### Data sensitivity

Persisted users, posts, scores, links, and tags are public Stack Overflow data. The database contains
no OpenAI key and no private Stack Overflow token.

### Denial-of-service controls

- 31-day hard date limit
- Six-page hard upstream limit
- At most 21 cohort user IDs
- At most 100 IDs per Stack Exchange batch
- Bounded retries
- Request and provider timeouts

Application-level authentication and rate limiting would still be needed before public deployment.

## 22. Local execution

### 22.1 Reviewer path: Docker and a browser

The only host prerequisite is Docker Desktop or Docker Engine with Compose:

```bash
docker compose up --build
```

Then open `http://127.0.0.1:8000`. Python, `uv`, WSL, curl, and jq are contained or unnecessary.
The Compose health check probes `/health`; `docker compose ps` reports container health. The named
volume preserves SQLite data across normal restarts.

### 22.2 Native execution with `uv`

From the repository root:

```bash
uv sync --frozen
uv run uvicorn stack_overflow_analyzer.main:app --host 127.0.0.1 --port 8000
```

Then open the product UI or API documentation:

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/docs
```

No `.env` file is needed for deterministic endpoints.

### 22.3 Optional environment file

macOS/Linux/WSL:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Set `SOA_OPENAI_API_KEY` only when using narratives.

### 22.4 Docker implementation details

```bash
docker compose up --build
```

The container:

- Uses Python 3.13 slim.
- Copies `uv` from the official Astral image.
- Installs from the committed lockfile.
- Uses a deny-by-default Docker build context that allowlists only `pyproject.toml`, `uv.lock`,
  `README.md`, and `src/`; local environments, caches, tests, databases, and secrets are excluded.
- Runs as non-root `appuser`.
- Starts the already-installed Uvicorn executable without modifying dependencies at runtime.
- Binds port 8000.
- Stores SQLite at `/data/stack_overflow.db`.
- Persists `/data` in the `analyzer-data` named volume.
- Reports health by probing `/health` with Python's standard library.
- Uses `restart: unless-stopped`.

### 22.5 Why `--reload` is omitted

The normal run command intentionally omits `--reload`. File watchers can encounter permission
problems when a checkout or cache is shared across Windows and WSL. Reload is a development
convenience, not a requirement for the service.

## 23. Demonstration commands

The preferred demonstration requires no commands after startup: open `http://127.0.0.1:8000`,
paste a profile URL, select a tag and month, and submit. The sections below demonstrate the same
contracts directly for API-focused review.

### Health

```bash
curl 'http://127.0.0.1:8000/health'
```

### Find candidate user IDs

```bash
curl 'http://127.0.0.1:8000/v1/tags/tensorflow/top-answerers/all-time'
```

### January benchmark leaderboard

```bash
curl --get 'http://127.0.0.1:8000/v1/tags/tensorflow/contributors' \
  --data-urlencode 'from_date=2025-01-01' \
  --data-urlencode 'to_date=2025-02-01' \
  --data-urlencode 'limit=20'
```

### Specified contributor

Replace `USER_ID` with an integer returned by the cohort endpoint:

```bash
curl --get 'http://127.0.0.1:8000/v1/tags/tensorflow/contributors/USER_ID' \
  --data-urlencode 'from_date=2025-01-01' \
  --data-urlencode 'to_date=2025-02-01'
```

### OpenAI narrative

```bash
curl -X POST 'http://127.0.0.1:8000/v1/tags/tensorflow/contributors/USER_ID/narrative' \
  -H 'Content-Type: application/json' \
  -d '{"start_date":"2025-01-01","end_date":"2025-02-01"}'
```

## 24. Testing strategy

All tests are offline. No test spends Stack Exchange quota or OpenAI credits.

### API tests

Verify:

- Health response
- Caller-provided request ID propagation
- Invalid tags and dates
- First-of-month half-open intervals
- 31-day hard limit on leaderboard and detail
- Official cohort endpoint
- Benchmark response fields and cohort context

### Date and analytics tests

Verify:

- Half-open UTC conversion
- Equal-length previous periods
- Empty and reversed range rejection
- Deterministic tie-breakers
- Official peer means
- Previous-period changes
- Related-tag shares
- Subjects outside the official cohort
- Missing subject behavior

### Repository and synchronization tests

Verify:

- Multi-user and question batching
- Tag filtering
- Idempotent repeated synchronization
- Six-page budget behavior
- Interrupted-page resume
- Malformed parent data without checkpoint advancement
- Persistent cohort reuse

### Stack Exchange adapter tests

Verify:

- 5xx retry
- transport retry
- no retry for deterministic 4xx
- user-ID and question-ID de-duplication/batching
- half-open epoch parameters
- provider backoff
- local quota short-circuit
- malformed wrapper rejection
- tag URL encoding

### Narrative tests

Verify:

- Unknown evidence IDs are rejected
- Known evidence IDs are accepted
- `responses.parse` is used
- Pydantic model is passed as `text_format`
- `store=False`
- Missing parsed output is rejected
- No-key behavior is controlled

### Commands

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pre-commit run --all-files
```

Pre-commit runs Ruff formatting, Ruff linting, and the complete test suite through `uv`.

## 25. Troubleshooting guide

### `Stack Exchange quota is exhausted`

The provider reported no remaining quota, or the in-memory client already observed zero. Cached and
completed periods remain usable, but a new synchronization cannot continue until quota is available.
The HTTP response is 429 with `Retry-After: 86400`.

### `benchmark request budget was reached`

The combined cohort produced more than six answer pages for that interval. Choose a shorter date
range. The service did not return incomplete analytics.

### A valid user has no qualifying answers

The analysis still returns HTTP 200. The user identity is resolved from Stack Exchange, metrics are
zero, `has_qualifying_answers` is `false`, period rank and rank movement are `null`, and related tags
are empty. The UI explains the empty period and omits meaningless peer percentages. A user ID that
does not exist on Stack Overflow still returns 404.

### `SOA_OPENAI_API_KEY is required`

Only the narrative endpoint needs the key. Add it to a private `.env` or environment variable and
restart the application. Deterministic GET endpoints work without it.

### Stack Exchange `access_denied`

The service does not send a Stack Overflow key or access token. Confirm that the base URL and site
configuration have not been overridden and that the copied curl command contains only the literal
URL—not Markdown `[label](url)` syntax.

### Curl reports a value such as `20curl`

The shell probably concatenated two pasted commands or remained inside an unmatched quote. Press
Ctrl+C and paste one complete command block at a time. Using `curl --get` with separate
`--data-urlencode` arguments avoids shell interpretation of `&`.

### Uvicorn watcher permission error

Run without `--reload`. If working across Windows and WSL, keep the checkout in the active operating
system's native filesystem or fix the ownership of tool-generated cache directories.

### Port 8000 is already in use

Choose another port:

```bash
uv run uvicorn stack_overflow_analyzer.main:app --host 127.0.0.1 --port 8001
```

Then update curl URLs accordingly.

### Need a clean local data snapshot

Stop the application and move the configured SQLite database to a backup location before restarting.
Moving rather than deleting preserves recoverability. Docker users can choose a new named volume or
back up the existing volume before resetting it.

## 26. Reviewer and interview questions

### Why Stack Overflow?

It provides public collaboration/knowledge-sharing data, stable entity IDs, native quality signals,
tags, accepted answers, and a rate-limited API that makes caching and integration design meaningful.

### Why Python?

FastAPI, Pydantic, async httpx, SQLAlchemy, and the OpenAI SDK provide a compact production-style
stack. Python is explicitly accepted by the assignment.

### Why not ask the LLM to rank contributors?

Arithmetic and ordering must be reproducible and testable. An LLM is useful for synthesis, not as a
database or calculator. Keeping metrics deterministic prevents hallucinated rankings and makes the
evidence chain auditable.

### Why use native answer score as the primary ranking signal?

It is an upstream fact understood by Stack Overflow users. A weighted combination of volume,
acceptance, and score would require arbitrary weights and could obscure why someone ranked highly.
Secondary metrics still explain whether rank came from volume, quality, or both.

### Why use the all-time Top-20 cohort?

Stack Exchange supplies it cheaply and authoritatively. It bounds the comparison while providing a
meaningful high-quality benchmark. The response labels the population honestly.

### Why not call this the period Top-20?

Because it is not. Discovering the true arbitrary-period Top-20 requires evaluating the entire tag
population. The code and response use `period_benchmark_rank` to avoid that false claim.

### Why include an outside subject?

The assignment's primary use case is a specified user. Adding that user allows comparison with the
benchmark without pretending they belong to the official all-time cohort.

### Why half-open dates?

Half-open intervals compose without gaps or overlaps and avoid ambiguous “end of day” timestamps.
The first day of one month to the first day of the next expresses a calendar month naturally.

### Why cap periods at 31 days and pages at six?

The date cap makes reviewer input predictable. The page cap provides the stronger guarantee: even a
very active cohort cannot consume an unbounded number of API requests.

### Why fetch parent questions?

The multi-user answer endpoint is not filtered by tag. Parent questions provide the authoritative
tags needed to determine whether an answer qualifies and which related topics characterize it.

### Why cache in SQLite rather than memory?

SQLite survives restarts, supports transactional checkpoints, requires no separate service, and is
appropriate for a local assignment. Memory-only caching would lose data and resumability whenever
the process exits.

### Why use ABC ports?

They keep FastAPI, Stack Exchange, SQLite, and OpenAI replaceable and testable without building an
excessive framework. Tests can inject fakes at the actual side-effect boundaries.

### Why is narrative a POST?

It initiates paid external computation and is not currently narrative-cached. GET semantics could
encourage automatic retries or prefetching that spends money unexpectedly.

### What makes synchronization safe after a crash?

Entity upserts and checkpoint advancement share one transaction. A crash before commit leaves the
page pending; a crash after commit leaves stable IDs and the advanced page together. Retrying cannot
create duplicates.

### What happens when the subject has no previous activity?

They remain in the previous comparison cohort with zero metrics and receive a deterministic rank
under the same tie-breakers. Changes are calculated from zero.

### Why mean rather than median for peers?

Inactive official leaders are excluded from the peer calculation, preventing zero rows from
dominating a sparse month. Among active peers, the arithmetic mean preserves the magnitude of their
activity. The selected contributor is excluded so they do not influence their own baseline. The
complete leaderboard still exposes inactive members and makes the population transparent.

### What would change for production scale?

Use Postgres, database-backed sync leases, background workers, explicit snapshot freshness policies,
authentication, application rate limiting, OpenTelemetry, Alembic migrations, narrative caching,
and an evaluation harness.

## 27. Suggested ten-minute demo and code tour

1. **Health and Swagger** — show `/health` and `/docs`.
2. **Official cohort** — call the all-time endpoint and choose a user ID.
3. **Deterministic analysis** — call the subject GET for a one-month half-open range.
4. **Explain the output** — distinguish official rank from period benchmark rank; show peer means
   and the previous-period comparison.
5. **Show caching** — repeat the request and point out that completed checkpoints avoid new provider
   calls.
6. **Narrative** — call POST when an OpenAI key is configured; show evidence IDs.
7. **Code tour** — walk through `api/app.py`, `application/analytics.py`, `application/sync.py`, and
   the three adapters.
8. **Trade-offs** — state clearly that the benchmark is not a global period Top-20 and explain the
   31-day/six-page safety bounds.

## 28. Final mental model

The shortest accurate explanation of the entire system is:

```text
Stack Exchange chooses the benchmark population.
The caller chooses the tag, subject, and bounded half-open period.
The async adapter fetches cohort activity safely.
SQLite makes the fetch idempotent and resumable.
Python establishes ranks, comparisons, trends, topics, and evidence.
FastAPI exposes the deterministic result.
OpenAI optionally explains it under a structured, evidence-validated contract.
```
