# Submission notes

## Run locally

The reviewer path requires Docker Desktop, or Docker Engine with the Compose plugin:

```bash
docker compose up --build
```

The included launcher scripts run the same command:

```powershell
# Windows PowerShell
.\run.ps1
```

```bash
# macOS or Linux
sh ./run.sh
```

Open `http://127.0.0.1:8000` for the UI or `http://127.0.0.1:8000/docs` for Swagger. Stack
Overflow analysis uses public data and requires no credentials. To enable the optional narrative,
set the API key as an environment variable in the terminal before starting the application:

```powershell
# Windows PowerShell
$env:SOA_OPENAI_API_KEY = "your-real-key-here"
.\run.ps1
```

```bash
# macOS or Linux
export SOA_OPENAI_API_KEY="your-real-key-here"
sh ./run.sh
```

The variable applies to the current terminal session and is passed into the container by Compose.
Do not commit the key. The deterministic analysis works without it; only narrative generation
requires OpenAI.

## Architecture and main decisions

FastAPI owns HTTP validation and error mapping. `AnalyticsService` calculates all metrics;
`SyncService` coordinates bounded, resumable synchronization. Async adapters isolate Stack
Exchange, SQLite, and OpenAI behind narrow ports, allowing the offline test suite to replace every
external boundary with fakes. SQLite persists public upstream entities, cohort snapshots, and
transactional page checkpoints so repeated requests reuse data and interrupted synchronization can
resume safely.

The primary metric is `period_benchmark_rank`. For each member of Stack Exchange's official
all-time Top-20 for a tag—plus the requested user when necessary—the service sums scores on answers
created during the requested period and ranks those individual totals. Accepted-answer count and
answer count break ties. This measures the user's relative answer-score impact among a historically
strong comparison group; it is not a global period leaderboard. 

The optional OpenAI endpoint receives only deterministic subject metrics, comparisons, topics, and
a closed evidence list. Pydantic Structured Outputs enforce the response shape.

## With another day

I would add richer ways to explore the results, especially interactive plots showing how scores,
answer volume, and accepted answers change over time. I would also let users compare multiple
periods or contributors, explore which topics drove the largest changes, and export or share a
short report of the most important insights.

## AI usage

I used Codex in VS Code across every stage of the assignment to accelerate my work: planning,
implementation, debugging, documentation, and review. I also used Codex's Planning mode to break
the work into steps and make design decisions before implementation, with particularly extensive
use for writing and strengthening unit tests. I then reviewed the generated changes.
