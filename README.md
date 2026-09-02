# Stack Overflow Contributor Analyzer

Compare one Stack Overflow contributor with the official all-time Top-20 answerers for a technology
tag during a selected calendar month. An OpenAI narrative summarizes the result.

## Run with Docker

Install Docker Desktop or Docker Engine with Compose.

### OpenAI API key

Set the key as an environment variable before starting the application. It is required only when
**Include AI narrative** is checked.

```powershell
# Windows PowerShell
$env:SOA_OPENAI_API_KEY = "your-real-key-here"
.\run.ps1
```

```bash
# macOS, Linux, or WSL
export SOA_OPENAI_API_KEY="your-real-key-here"
sh run.sh
```

Create a key in the [OpenAI API dashboard](https://platform.openai.com/api-keys). If no key is
available, run the same script without setting the variable and uncheck **Include AI narrative**.

### Use the application

Open <http://127.0.0.1:8000>, then:

1. Paste a Stack Overflow profile URL or numeric user ID.
2. Enter a tag such as `keras`, `tensorflow`, or `pytorch`.
3. Choose a month.
4. Choose whether to include the AI narrative.
5. Select **Analyze contribution**.

Stop the service with Ctrl+C or `docker compose down`.

## Metric

The benchmark population is Stack Exchange's official all-time Top-20 answerers for the requested
tag, plus the selected contributor when they are outside that cohort. Ranking uses answer post
score, accepted-answer count, answer count, and user ID as documented tie-breakers.

Stack Overflow's answer `score` is the post's net vote score. 