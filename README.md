# Stack Overflow Contributor Analyzer

A small FastAPI service that compares one Stack Overflow contributor with historically strong
answerers for a technology tag during a selected period. Metrics are calculated deterministically;
an optional OpenAI narrative explains the result using a validated evidence chain.

## Run in 60 seconds

Prerequisite: Docker Desktop, or Docker Engine with the Compose plugin.

```bash
docker compose up --build
```

Open <http://127.0.0.1:8000>, then enter a Stack Overflow profile or user ID, a tag, and a month.
Swagger is available at <http://127.0.0.1:8000/docs>.

Stack Overflow analysis needs no credentials. To enable the optional narrative, set the API key as
an environment variable before startup:

```powershell
# Windows PowerShell
$env:SOA_OPENAI_API_KEY = "your-real-key-here"
```

```bash
# macOS or Linux
export SOA_OPENAI_API_KEY="your-real-key-here"
```

The deterministic result remains available when OpenAI is unconfigured. Other optional settings
and their defaults are listed in `.env.example`.

## Call the API directly

Dates are a half-open UTC interval: `from_date` is included and `to_date` is excluded.

```bash
curl --get 'http://127.0.0.1:8000/v1/tags/keras/contributors/10908375' \
  --data-urlencode 'from_date=2020-08-01' \
  --data-urlencode 'to_date=2020-09-01'

curl -X POST 'http://127.0.0.1:8000/v1/tags/keras/contributors/10908375/narrative' \
  -H 'Content-Type: application/json' \
  -d '{"start_date":"2020-08-01","end_date":"2020-09-01"}'
```

The first endpoint returns the numerical analysis. The second returns that same analysis plus a
short narrative, an optional evidence-supported root-cause hypothesis, confidence, and evidence
IDs.

## What the main metric means

For every member of Stack Exchange's official all-time Top-20 for the tag, plus the requested user
when necessary, the service sums scores on answers created during the selected period. It ranks
those individual totals from highest to lowest; acceptance count and answer count break ties.

The resulting `period_benchmark_rank` indicates the user's answer-score position among that
historically strong comparison group. It is not a global rank among everyone who used the tag.
Answer counts, acceptance rate, average score, peer averages, and previous-period change explain
the result rather than forming an opaque weighted score.

## Development checks

Native development requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

At a high level, FastAPI owns HTTP validation, application services own calculation and bounded
synchronization, SQLite provides persistent caching and resumable checkpoints, and async adapters
isolate Stack Exchange and OpenAI. See `NOTES.md` for architecture decisions, trade-offs, deferred
work, and AI-assistant disclosure.
