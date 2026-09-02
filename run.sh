#!/usr/bin/env sh
set -eu

export BUILDX_GIT_INFO=0
exec docker compose up --build "$@"
