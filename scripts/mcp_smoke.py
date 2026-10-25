"""MCP stdio smoke test: spawn the civ6 server from .mcp.json and list its tools.

Usage (from WSL or any shell, stdlib only):
    python3 scripts/mcp_smoke.py [--timeout SECONDS]

Spawns the "civ6" server command from the repo's .mcp.json, sends
initialize, notifications/initialized and tools/list as newline-delimited
JSON-RPC (MCP stdio framing, no Content-Length headers), prints
serverInfo.name and the tool count, and exits 0. The game does not need to
be running. Server stderr goes to a temp log, printed on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_command() -> list[str]:
    with open(os.path.join(REPO, ".mcp.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    server = cfg["mcpServers"]["civ6"]
    return [server["command"], *server.get("args", [])]


def reader_thread(stream, out: queue.Queue) -> None:
    for raw in iter(stream.readline, b""):
        out.put(raw)
    out.put(None)


def wait_for_id(lines: queue.Queue, want_id: int, deadline: float) -> dict:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no response with id={want_id} before timeout")
        try:
            raw = lines.get(timeout=remaining)
        except queue.Empty:
            continue
        if raw is None:
            raise RuntimeError(f"server closed stdout before id={want_id}")
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            print(f"(non-JSON stdout line ignored: {text[:120]!r})", file=sys.stderr)
            continue
        if msg.get("id") == want_id:
            if "error" in msg:
                raise RuntimeError(f"id={want_id} error: {msg['error']}")
            return msg["result"]


def send(proc: subprocess.Popen, obj: dict) -> None:
    proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
    proc.stdin.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    cmd = load_command()
    print(f"spawning: {cmd}", file=sys.stderr)
    errlog = tempfile.NamedTemporaryFile(
        prefix="mcp_smoke_stderr_", suffix=".log", delete=False
    )
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errlog
    )
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=reader_thread, args=(proc.stdout, lines), daemon=True).start()
    deadline = time.monotonic() + args.timeout

    ok = False
    try:
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0"},
                },
            },
        )
        init = wait_for_id(lines, 1, deadline)
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = wait_for_id(lines, 2, deadline)
        print(f"serverInfo.name: {init['serverInfo']['name']}")
        print(f"tool count: {len(tools['tools'])}")
        ok = True
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
    finally:
        # Closing stdin ends the stdio session so the lifespan shuts down cleanly.
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        errlog.close()
        if not ok:
            with open(errlog.name, encoding="utf-8", errors="replace") as f:
                tail = f.read()[-4000:]
            print(f"--- server stderr ({errlog.name}) ---\n{tail}", file=sys.stderr)
        else:
            os.unlink(errlog.name)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
