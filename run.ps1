$ErrorActionPreference = "Stop"
$env:BUILDX_GIT_INFO = "0"
docker compose up --build @args
exit $LASTEXITCODE
