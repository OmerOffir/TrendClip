#!/usr/bin/env bash
# TrendClipper runner. Sets up .venv + dependencies on first use, then runs the app.
#
#   ./run.sh                    # web dashboard at http://127.0.0.1:8000 (default) + the Discord
#                               # bot when DISCORD_BOT_TOKEN is set (log: output/discord_bot.log)
#   ./run.sh web --port 8080    # dashboard on another port (--reload for dev; NO_BOT=1 skips the bot)
#   ./run.sh bot                # only the Discord bot (DISCORD_BOT_TOKEN etc. in .env)
#   ./run.sh cli [options]      # one pipeline run in the terminal, e.g. --category 28
#   ./run.sh assemble --demo    # 9:16 Short with karaoke subtitles -> output/final_short.mp4
#                               # (real input: --voice voice.mp3 --timestamps words.json)
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

BOT_PID_FILE="output/discord_bot.pid"
BOT_LOG="output/discord_bot.log"

bot_running() {
  [[ -f "$BOT_PID_FILE" ]] && kill -0 "$(cat "$BOT_PID_FILE")" 2>/dev/null
}

# The dashboard brings the bot along (two bots on one token would both answer, so only one runs).
start_bot() {
  if [[ -n "${NO_BOT:-}" ]] || ! grep -qE '^DISCORD_BOT_TOKEN=.+' .env; then
    return
  fi
  if bot_running; then
    echo "==> Discord bot already running (pid $(cat "$BOT_PID_FILE"))"
    return
  fi
  mkdir -p output
  "$PY" run.py --bot >>"$BOT_LOG" 2>&1 &
  local pid=$!
  echo "$pid" > "$BOT_PID_FILE"
  trap 'kill '"$pid"' 2>/dev/null; rm -f "'"$BOT_PID_FILE"'"' EXIT
  echo "==> Discord bot started (pid $pid, log $BOT_LOG)"
}

cmd="${1:-web}"
[[ $# -gt 0 ]] && shift

case "$cmd" in
  web)   setup; start_bot; "$PY" run.py --web "$@" ;;
  bot)
    setup
    if bot_running; then
      echo "!! The Discord bot is already running (pid $(cat "$BOT_PID_FILE")); stop it first." >&2
      exit 1
    fi
    mkdir -p output
    echo $$ > "$BOT_PID_FILE"
    trap 'rm -f "$BOT_PID_FILE"' EXIT
    "$PY" run.py --bot "$@" ;;
  cli)   setup; exec "$PY" run.py "$@" ;;
  assemble) setup; exec "$PY" -m trendclip.video_assembler "$@" ;;
  test)  setup; exec "$PY" -m pytest -q "$@" ;;
  setup) setup; echo "==> Environment ready" ;;
  -h|--help|help)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)
    echo "Unknown command: $cmd (use: web | bot | cli | assemble | test | setup | help)" >&2
    exit 2 ;;
esac
