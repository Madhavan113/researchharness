"""A trial-bound command endpoint. All execution goes through Harbor's environment.

The controller owns this server and its receipts; workers receive only the
endpoint credential. No endpoint accepts host paths, evaluator actions or review
decisions. Retried request IDs return their receipt instead of executing again.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

from research_harness.util import canonical_json, timestamp, write_json

OUTPUT_LIMIT = 65536


class WorkspaceBridge:
    def __init__(self, environment, output: Path, *, max_commands: int, timeout: int):
        if type(max_commands) is not int or not 1 <= max_commands <= 100:
            raise ValueError("Choose between 1 and 100 workspace commands")
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError("Workspace deadline must be between 1 and 3600 seconds")
        self.environment = environment
        self.output = output
        self.output.mkdir(parents=True, exist_ok=False)
        self.max_commands = max_commands
        self.deadline = time.monotonic() + timeout
        self.loop = asyncio.get_running_loop()
        self.key = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.count = 0
        self.closed = False
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if self.path != "/execute" or not secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + bridge.key
                ):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= OUTPUT_LIMIT:
                        raise ValueError("Invalid command request size")
                    request = json.loads(self.rfile.read(length))
                    result = bridge.execute(request)
                    body = canonical_json(result).encode()
                    self.send_response(200)
                except (ValueError, OSError) as exc:
                    body = canonical_json({"error": str(exc)}).encode()
                    self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/execute"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def execute(self, request: dict) -> dict:
        if not isinstance(request, dict) or set(request) != {"id", "command", "timeout"}:
            raise ValueError("Workspace requests require only id, command and timeout")
        if not isinstance(request["id"], str) or str(UUID(request["id"])) != request["id"]:
            raise ValueError("Workspace request id must be a canonical UUID")
        if not isinstance(request["command"], str) or not request["command"].strip():
            raise ValueError("Workspace command must be nonempty")
        if type(request["timeout"]) is not int or not 1 <= request["timeout"] <= 60:
            raise ValueError("Command timeout must be between 1 and 60 seconds")
        with self.lock:
            if self.closed:
                raise ValueError("Workspace endpoint is closed")
            path = self.output / f"{request['id']}.json"
            if path.exists():
                previous = json.loads(path.read_bytes())
                if previous["request"] != request:
                    raise ValueError("Request id already belongs to another command")
                return previous
            remaining = int(self.deadline - time.monotonic())
            if remaining < 1 or self.count >= self.max_commands:
                raise ValueError("Workspace execution limit reached")
            self.count += 1
            record = {
                "request": request,
                "context_id": str(self.environment.context_id),
                "environment_session_id": self.environment.session_id,
                "started_at": timestamp(),
                "status": "running",
            }
            write_json(path, record)
            future = asyncio.run_coroutine_threadsafe(
                self.environment.exec(
                    request["command"], timeout_sec=min(request["timeout"], remaining)
                ),
                self.loop,
            )
            try:
                result = future.result(timeout=min(request["timeout"], remaining) + 5)
                record.update(status="completed", exit_code=result.return_code)
                for name in ("stdout", "stderr"):
                    raw = (getattr(result, name) or "").encode()
                    record[name] = raw[:OUTPUT_LIMIT].decode(errors="replace")
                    record[name + "_bytes"] = len(raw)
                    record[name + "_truncated"] = len(raw) > OUTPUT_LIMIT
            except Exception as exc:
                future.cancel()
                record.update(status="error", error=str(exc), execution_outcome="unresolved")
            record["finished_at"] = timestamp()
            write_json(path, record)
            return record

    async def close(self):
        self.closed = True
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        # Drain admitted work before the evaluator can collect candidate artifacts.
        await asyncio.to_thread(self.lock.acquire)
        self.lock.release()
        self.thread.join(timeout=5)
