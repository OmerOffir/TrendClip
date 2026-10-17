"""A public link to one local file for a few minutes, for APIs that download from a URL (Instagram).

A tiny HTTP server on 127.0.0.1 serves only that file, at a random secret path; a Cloudflare quick
tunnel (`cloudflared`, free, no account) gives it a temporary https://<random>.trycloudflare.com
address. Both stop when the `with` block ends. Nothing else on the machine is reachable through it.

Install cloudflared once: macOS `brew install cloudflared`; Debian/Ubuntu: the .deb from
https://github.com/cloudflare/cloudflared/releases (`sudo dpkg -i cloudflared-linux-amd64.deb`).
"""

from __future__ import annotations

import logging
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

START_TIMEOUT = 45
READY_TIMEOUT = 90
CHUNK = 1024 * 1024
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    pass


def cloudflared_path() -> str | None:
    return os.getenv("CLOUDFLARED") or shutil.which("cloudflared") or next(
        (p for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared", "/usr/bin/cloudflared")
         if Path(p).is_file()), None)


def _handler(path: Path, route: str, mime: str) -> type[BaseHTTPRequestHandler]:
    size = path.stat().st_size

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.debug("tunnel: " + fmt, *args)

        def _range(self) -> tuple[int, int] | None:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
            if not m or not (m.group(1) or m.group(2)):
                return None
            if not m.group(1):
                return max(size - int(m.group(2)), 0), size - 1
            return int(m.group(1)), min(int(m.group(2)) if m.group(2) else size - 1, size - 1)

        def _send(self, body: bool) -> None:
            if self.path.split("?", 1)[0] != route:
                self.send_error(404)
                return
            span = self._range()
            start, end = span or (0, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206 if span else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if span:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not body:
                return
            with path.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0 and (chunk := f.read(min(CHUNK, left))):
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def do_GET(self):
            self._send(True)

        def do_HEAD(self):
            self._send(False)

    return Handler


def _read_url(proc: subprocess.Popen) -> str:
    lines: queue.Queue[str] = queue.Queue()

    def pump():
        for line in proc.stderr:
            lines.put(line)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + START_TIMEOUT
    seen = []
    while time.monotonic() < deadline:
        if proc.poll() is not None and lines.empty():
            break
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        seen.append(line.strip())
        if m := _URL.search(line):
            return m.group(0)
    raise TunnelError("cloudflared didn't open a tunnel: " + (" | ".join(seen[-3:]) or "no output"))


def _wait_ready(url: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    last = ""
    while time.monotonic() < deadline:
        try:
            resp = requests.head(url, timeout=10)
            if resp.status_code == 200:
                return
            last = f"HTTP {resp.status_code}"
        except requests.RequestException as err:
            last = str(err)
        time.sleep(2)
    raise TunnelError(f"The temporary link didn't come up in {READY_TIMEOUT} s ({last[:120]})")


@contextmanager
def public_file(path: Path, mime: str = "video/mp4") -> Iterator[str]:
    """Yield a temporary public https URL serving only `path`; closed when the block ends."""
    binary = cloudflared_path()
    if not binary:
        raise TunnelError("Install cloudflared once to post videos on Instagram: macOS `brew install cloudflared`; "
                          "Linux: the .deb from github.com/cloudflare/cloudflared/releases (README -> Instagram)")
    route = f"/{secrets.token_urlsafe(24)}/{quote(path.name)}"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(path, route, mime))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    proc = subprocess.Popen([binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        url = _read_url(proc) + route
        _wait_ready(url)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        server.server_close()
