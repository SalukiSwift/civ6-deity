"""Drive the civ6 MCP server through a script of steps, one server process.

Usage (from WSL or any shell, stdlib only):

    # Run a fixed script and exit:
    python3 scripts/mcp_drive.py run STEPS.jsonl [--out OUT.log] [--stderr ERR.log]

    # Keep the server alive and follow STEPS.jsonl for new lines (run in the
    # background); stops at a {"quit": true} step or when killed:
    python3 scripts/mcp_drive.py run STEPS.jsonl --follow --out OUT.log --stderr ERR.log

    # Append one step to a followed STEPS.jsonl and wait for its result:
    python3 scripts/mcp_drive.py send STEPS.jsonl OUT.log '{"tool": "get_situation"}'

Each line of STEPS.jsonl is one JSON object, one of:

    {"tool": "<name>", "args": {...}, "timeout": 600}   tools/call, prints the result text
    {"shell": "<command>", "timeout": 120}               runs a shell command between calls
    {"sleep": N}                                          waits N seconds
    {"list_tools": true}                                  tools/list, prints the tool names
    {"note": "<text>"}                                    prints a note into the log
    {"quit": true}                                        ends a --follow run

Blank lines and lines starting with "#" are skipped. An optional "id" names
a step; `send` adds one and waits for it. Every step is written to OUT as a
block:

    === STEP <n> id=<id> <kind> <detail> ===
    <output>
    === END STEP <n> id=<id> latency=<s>s error=<bool> ===

and a timing row is appended to OUT.timings.jsonl. The server command comes
from the repo's .mcp.json, as in mcp_smoke.py. Server stderr goes to ERR.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Generous defaults: end_turn on Deity takes 30 to 90 s, new_game about a
# minute, restart_and_load several minutes.
DEFAULT_TIMEOUTS = {
    "new_game": 600.0,
    "end_turn": 600.0,
    "restart_and_load": 1500.0,
    "launch_game": 600.0,
    "load_game_save": 900.0,
    "load_save_from_menu": 900.0,
}
DEFAULT_TIMEOUT = 300.0


def load_command() -> list[str]:
    with open(os.path.join(REPO, ".mcp.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    server = cfg["mcpServers"]["civ6"]
    return [server["command"], *server.get("args", [])]


class Server:
    """One MCP stdio server process with a reader thread."""

    def __init__(self, stderr_path: str) -> None:
        self.cmd = load_command()
        self.errlog = open(stderr_path, "ab")
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.errlog,
        )
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self.next_id = 1

    def _reader(self) -> None:
        for raw in iter(self.proc.stdout.readline, b""):
            self.lines.put(raw)
        self.lines.put(None)

    def send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float) -> dict:
        rid = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method}: no response in {timeout:.0f}s")
            try:
                raw = self.lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError(
                        f"server exited (code {self.proc.returncode}) during {method}"
                    )
                continue
            if raw is None:
                raise RuntimeError(f"server closed stdout during {method}")
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            # A request from the server (ping, roots/list): answer empty.
            if "method" in msg and "id" in msg:
                self.send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"{method} error: {msg['error']}")
                return msg["result"]

    def initialize(self) -> dict:
        init = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mcp_drive", "version": "0"},
            },
            timeout=180.0,
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return init

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.errlog.close()


def iter_steps(path: str, follow: bool):
    """Yield parsed steps from path; with follow, keep tailing for new lines."""
    pos = 0
    buf = ""
    while True:
        try:
            with open(path, encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
        except FileNotFoundError:
            chunk = ""
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                yield {"note": f"BAD STEP LINE ({e}): {line[:200]}"}
        if not follow:
            if buf.strip():
                try:
                    yield json.loads(buf.strip())
                except json.JSONDecodeError:
                    pass
            return
        time.sleep(0.3)


def run(args: argparse.Namespace) -> int:
    out = open(args.out, "a", encoding="utf-8") if args.out else sys.stdout
    timings_path = (args.out or "mcp_drive") + ".timings.jsonl"

    def emit(text: str) -> None:
        out.write(text + "\n")
        out.flush()

    stderr_path = args.stderr or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), f"mcp_drive_stderr_{os.getpid()}.log"
    )
    emit(f"# mcp_drive start {time.strftime('%Y-%m-%d %H:%M:%S')} stderr={stderr_path}")
    server = Server(stderr_path)
    t0 = time.monotonic()
    try:
        init = server.initialize()
    except Exception as e:  # noqa: BLE001
        emit(f"FAIL initialize: {e}")
        server.close()
        return 1
    emit(
        f"# initialized: {init['serverInfo']['name']} in {time.monotonic() - t0:.1f}s"
    )

    n = 0
    for step in iter_steps(args.steps, args.follow):
        n += 1
        sid = step.get("id", "")
        if step.get("quit"):
            emit(f"=== STEP {n} id={sid} quit ===")
            emit(f"=== END STEP {n} id={sid} latency=0.00s error=False ===")
            break
        start_wall = time.strftime("%H:%M:%S")
        t = time.monotonic()
        is_error = False
        kind = ""
        body = ""
        if "tool" in step:
            name = step["tool"]
            targs = step.get("args", {}) or {}
            kind = f"tool={name} args={json.dumps(targs, ensure_ascii=False)}"
            timeout = float(
                step.get("timeout", DEFAULT_TIMEOUTS.get(name, DEFAULT_TIMEOUT))
            )
            emit(f"=== STEP {n} id={sid} [{start_wall}] {kind} ===")
            try:
                res = server.request(
                    "tools/call", {"name": name, "arguments": targs}, timeout
                )
                is_error = bool(res.get("isError"))
                parts = []
                for c in res.get("content", []):
                    if c.get("type") == "text":
                        parts.append(c["text"])
                    else:
                        parts.append(f"<{c.get('type')} content>")
                body = "\n".join(parts)
            except Exception as e:  # noqa: BLE001
                is_error = True
                body = f"DRIVER ERROR: {e}"
        elif "shell" in step:
            kind = f"shell={step['shell']}"
            emit(f"=== STEP {n} id={sid} [{start_wall}] {kind} ===")
            try:
                cp = subprocess.run(
                    step["shell"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=float(step.get("timeout", 300)),
                )
                is_error = cp.returncode != 0
                body = (cp.stdout + cp.stderr).rstrip() + f"\n(exit {cp.returncode})"
            except subprocess.TimeoutExpired:
                is_error = True
                body = "DRIVER ERROR: shell timeout"
        elif "sleep" in step:
            kind = f"sleep={step['sleep']}"
            emit(f"=== STEP {n} id={sid} [{start_wall}] {kind} ===")
            time.sleep(float(step["sleep"]))
        elif step.get("list_tools"):
            kind = "list_tools"
            emit(f"=== STEP {n} id={sid} [{start_wall}] {kind} ===")
            try:
                res = server.request("tools/list", {}, 120.0)
                names = sorted(tl["name"] for tl in res["tools"])
                body = f"tool count: {len(names)}\n" + "\n".join(names)
            except Exception as e:  # noqa: BLE001
                is_error = True
                body = f"DRIVER ERROR: {e}"
        elif "note" in step:
            kind = "note"
            emit(f"=== STEP {n} id={sid} [{start_wall}] note ===")
            body = str(step["note"])
        else:
            kind = "unknown"
            emit(f"=== STEP {n} id={sid} [{start_wall}] unknown ===")
            body = f"unknown step: {json.dumps(step)}"
            is_error = True
        latency = time.monotonic() - t
        if body:
            emit(body)
        emit(f"=== END STEP {n} id={sid} latency={latency:.3f}s error={is_error} ===")
        with open(timings_path, "a", encoding="utf-8") as tf:
            tf.write(
                json.dumps(
                    {
                        "n": n,
                        "id": sid,
                        "start": start_wall,
                        "kind": kind[:200],
                        "latency_s": round(latency, 3),
                        "error": is_error,
                    }
                )
                + "\n"
            )
        if server.proc.poll() is not None:
            emit(f"# server exited with code {server.proc.returncode}")
            break
    server.close()
    emit(f"# mcp_drive end {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


def send(args: argparse.Namespace) -> int:
    step = json.loads(args.step)
    sid = step.get("id") or uuid.uuid4().hex[:8]
    step["id"] = sid
    with open(args.steps, "a", encoding="utf-8") as f:
        f.write(json.dumps(step, ensure_ascii=False) + "\n")
    end_re = re.compile(rf"^=== END STEP \d+ id={re.escape(sid)} .*===$", re.M)
    start_re = re.compile(rf"^=== STEP \d+ id={re.escape(sid)} .*===$", re.M)
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        try:
            with open(args.out, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        m_end = end_re.search(text)
        if m_end:
            m_start = start_re.search(text)
            print(text[m_start.start() if m_start else 0 : m_end.end()])
            return 0
        time.sleep(0.5)
    print(f"(no result for step id={sid} within {args.wait:.0f}s; still running?)")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("steps")
    r.add_argument("--out")
    r.add_argument("--stderr")
    r.add_argument("--follow", action="store_true")
    s = sub.add_parser("send")
    s.add_argument("steps")
    s.add_argument("out")
    s.add_argument("step")
    s.add_argument("--wait", type=float, default=1500.0)
    args = ap.parse_args()
    return run(args) if args.cmd == "run" else send(args)


if __name__ == "__main__":
    sys.exit(main())
