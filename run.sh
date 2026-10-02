#!/usr/bin/env bash
# TrendClipper runner. Sets up .venv + dependencies on first use, then runs the app.
#
#   ./run.sh                    # web dashboard at http://127.0.0.1:8000 (default)
#   ./run.sh web --port 8080    # dashboard on another port (--reload for dev)
#   ./run.sh cli [options]      # one pipeline run in the terminal, e.g. --category 28
#   ./run.sh test               # unit tests
#   ./run.sh setup              # only create/refresh the environment
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
PY="$VENV/bin/python"
STAMP="$VENV/.requirements.sha"

hash_file() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1
  fi
}

setup() {
  if [[ ! -x "$PY" ]]; then
    echo "==> Creating virtual environment in $VENV"
    python3 -m venv "$VENV"
  fi

  local want have=""
  want="$(hash_file requirements.txt)"
  [[ -f "$STAMP" ]] && have="$(cat "$STAMP")"
  if [[ "$want" != "$have" ]]; then
    echo "==> Installing dependencies"
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q -r requirements.txt
    echo "$want" > "$STAMP"
  fi

  if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "==> Created .env from .env.example"
  fi
  if ! grep -qE '^YOUTUBE_API_KEY=.+' .env; then
    echo "!! YOUTUBE_API_KEY is empty in .env - add your key before running." >&2
    exit 1
  fi
}

cmd="${1:-web}"
[[ $# -gt 0 ]] && shift

case "$cmd" in
  web)   setup; exec "$PY" run.py --web "$@" ;;
  cli)   setup; exec "$PY" run.py "$@" ;;
  test)  setup; exec "$PY" -m pytest -q "$@" ;;
  setup) setup; echo "==> Environment ready" ;;
  -h|--help|help)
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)
    echo "Unknown command: $cmd (use: web | cli | test | setup | help)" >&2
    exit 2 ;;
esac
