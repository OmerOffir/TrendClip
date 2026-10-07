#!/usr/bin/env python3
"""Project runner: `python3 run.py [--web] [--test] [pipeline options]`.

Uses the project's .venv automatically, so activation isn't required.

Examples:
    python3 run.py --web                   # start the dashboard at http://127.0.0.1:8000
    python3 run.py --web --port 8080
    python3 run.py --bot                   # Discord bot (DISCORD_BOT_TOKEN in .env)
    python3 run.py                         # run the pipeline once (CLI, from .env)
    python3 run.py --test                  # run unit tests, then the pipeline
    python3 run.py --test-only             # unit tests only
    python3 run.py --regions US,GB --category 28 --top 5  # CLI options are passed through
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
DEFAULT_PORT = 8000


def _ensure_venv() -> None:
    if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])


def _run_tests() -> int:
    print("Running unit tests...", flush=True)
    return subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT)


def _pop_flag(args: list[str], flag: str) -> bool:
    if flag in args:
        args.remove(flag)
        return True
    return False


def _pop_option(args: list[str], name: str, default: str) -> str:
    if name in args:
        i = args.index(name)
        value = args[i + 1] if i + 1 < len(args) else default
        del args[i : i + 2]
        return value
    return default


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _run_web(args: list[str]) -> int:
    import uvicorn

    host = _pop_option(args, "--host", "127.0.0.1")
    explicit_port = "--port" in args
    port = int(_pop_option(args, "--port", str(DEFAULT_PORT)))
    reload = _pop_flag(args, "--reload")

    if not _port_free(host, port):
        fallback = None
        if not explicit_port:
            fallback = next((p for p in range(port + 1, port + 20) if _port_free(host, p)), None)
        if fallback is None:
            print(
                f"Port {port} is already in use by another program.\n"
                f"  See what it is: lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
                f"  Or use another port: ./run.sh web --port {port + 1}",
                file=sys.stderr,
            )
            return 1
        print(f"Port {port} is busy (another program is using it); using {fallback} instead.", flush=True)
        port = fallback
    print(f"TrendClipper dashboard: http://{host}:{port}", flush=True)
    uvicorn.run("trendclip.web.app:app", host=host, port=port, reload=reload, app_dir=str(ROOT))
    return 0


def main() -> int:
    _ensure_venv()
    args = sys.argv[1:]
    test_only = _pop_flag(args, "--test-only")
    with_tests = _pop_flag(args, "--test") or test_only
    web = _pop_flag(args, "--web")
    bot = _pop_flag(args, "--bot")

    if with_tests:
        code = _run_tests()
        if code != 0 or test_only:
            return code
        print()

    sys.path.insert(0, str(ROOT))
    if web:
        return _run_web(args)
    if bot:
        from trendclip.discord_bot import main as bot_main

        return bot_main(args)

    from trendclip.main import main as pipeline_main

    return pipeline_main(args)


if __name__ == "__main__":
    sys.exit(main())
