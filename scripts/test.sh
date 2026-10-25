#!/usr/bin/env bash
set -e; cd "$(dirname "$0")/.."
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/civ6-deity}"
exec uv run pytest -q "${@:-tests}"
