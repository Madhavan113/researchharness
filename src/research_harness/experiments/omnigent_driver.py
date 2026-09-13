"""Run a trusted experiment bundle using the pinned Omnigent runtime.

Invoked with Omnigent's separate interpreter. Its existing local-server helpers
own server/runner startup; session operations use its normal REST API. No source
discovery case or candidate-supplied host module is involved.
"""

from __future__ import annotations

import importlib.metadata
import json
import signal
import sys
import threading
import time
from pathlib import Path

import httpx

from research_harness.experiments.bundle import tool_policy
from research_harness.util import canonical_json, timestamp, write_json


def pages(client: httpx.Client, path: str, **params) -> list[dict]:
    result = []
    while True:
        response = client.get(path, params={"limit": 1000, **params})
        response.raise_for_status()
        page = response.json()
        result.extend(page["data"])
        if not page.get("has_more"):
            return result
        if not page["data"]:
            raise ValueError("Omnigent returned an empty page with has_more")
        params["after"] = page["data"][-1]["id"]


def run(config: dict) -> dict:
    from omnigent.chat import (
        _bundle_agent,
        _find_free_port,
        _start_local_server,
        _stop_local_server,
        _wait_for_server,
    )

    output = Path(config["output"])
    output.mkdir(parents=True, exist_ok=False)
    record = {"status": "starting", "started_at": timestamp()}
    write_json(
        output / "dependencies.json",
        {
            "python": sys.version,
            "executable": sys.executable,
            "distributions": {
                d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
            },
        },
    )
    write_json(output / "runtime.json", record)
    bundle = Path(config["bundle"])
    port = _find_free_port()
    server = None
    client = None
    stopping = threading.Event()
    ready = threading.Event()
    terminal = threading.Event()
    stream_errors = []
    reader = None
    sessions = []

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        server = _start_local_server(bundle, port, ephemeral=False)
        _wait_for_server(port, server)
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Origin": "omnigent://internal"},
            timeout=15,
            trust_env=False,
        )
        created = client.post(
            "/v1/sessions",
            data={"metadata": json.dumps({"title": "Research experiment"})},
            files={"bundle": ("experiment.tar.gz", _bundle_agent(bundle), "application/gzip")},
        )
        created.raise_for_status()
        session_id = created.json()["session_id"]
        bound = client.patch(f"/v1/sessions/{session_id}", json={"runner_id": server.runner_id})
        bound.raise_for_status()
        policy = tool_policy()
        installed = client.post(f"/v1/sessions/{session_id}/policies", json=policy)
        installed.raise_for_status()
        write_json(output / "tool-policy.json", {"submitted": policy, "receipt": installed.json()})
        record.update(
            status="running",
            session_id=session_id,
            runner_id=server.runner_id,
            server_url=str(client.base_url),
            server_pid=server.proc.pid,
            runner_pid=server.runner_proc.pid,
        )
        write_json(output / "runtime.json", record)

        def subscribe():
            try:
                with client.stream(
                    "GET", f"/v1/sessions/{session_id}/stream", timeout=None
                ) as stream:
                    stream.raise_for_status()
                    ready.set()
                    event = None
                    with (output / "events.jsonl").open("a") as handle:
                        for line in stream.iter_lines():
                            if stopping.is_set():
                                return
                            if line.startswith("event:"):
                                event = line[6:].strip()
                            elif line.startswith("data:"):
                                data = json.loads(line[5:].strip())
                                handle.write(canonical_json({"event": event, "data": data}) + "\n")
                                handle.flush()
                                if event in {
                                    "response.completed",
                                    "response.failed",
                                    "response.error",
                                }:
                                    if event != "response.completed":
                                        stream_errors.append(event)
                                    terminal.set()
                if not stopping.is_set():
                    stream_errors.append("Omnigent event stream ended")
            except Exception as exc:
                if not stopping.is_set():
                    stream_errors.append(str(exc))
            finally:
                ready.set()

        reader = threading.Thread(target=subscribe, daemon=True)
        reader.start()
        if not ready.wait(10) or stream_errors:
            raise RuntimeError(f"Cannot record Omnigent events: {stream_errors}")
        submitted = client.post(
            f"/v1/sessions/{session_id}/events",
            json={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": config["instruction"]}],
                },
            },
        )
        submitted.raise_for_status()
        write_json(output / "submission.json", submitted.json())
        deadline = time.monotonic() + config["timeout"]
        quiet_since = None
        while time.monotonic() < deadline:
            sessions = pages(client, "/v1/sessions", kind="any", order="asc")
            write_json(output / "sessions.json", sessions)
            if stream_errors:
                raise RuntimeError(f"Omnigent event stream failed: {stream_errors}")
            if any(row["status"] in {"failed", "error"} for row in sessions):
                raise RuntimeError("An Omnigent session failed; inspect its retained items")
            if terminal.is_set() and sessions and all(row["status"] == "idle" for row in sessions):
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= 1:
                    break
            else:
                quiet_since = None
            time.sleep(0.2)
        else:
            raise TimeoutError("Omnigent execution is unresolved; do not replay this attempt")
        record.update(status="completed", delegation_observed=len(sessions) > 1)
    except BaseException as exc:
        record.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "error", error=str(exc)
        )
    finally:
        if client is not None:
            try:
                sessions = pages(client, "/v1/sessions", kind="any", order="asc")
                write_json(output / "sessions.json", sessions)
                for session in sessions:
                    write_json(
                        output / "items" / f"{session['id']}.json",
                        pages(
                            client,
                            f"/v1/sessions/{session['id']}/items",
                            order="asc",
                        ),
                    )
            except Exception as exc:
                record["export_error"] = str(exc)
                record["status"] = "error"
        stopping.set()
        if server is not None:
            try:
                _stop_local_server(server)
            except Exception as exc:
                record.update(status="error", cleanup_error=str(exc))
        if reader is not None:
            reader.join(timeout=5)
        if client is not None:
            client.close()
        record["finished_at"] = timestamp()
        write_json(output / "runtime.json", record)
    return record


if __name__ == "__main__":
    result = run(json.loads(Path(sys.argv[1]).read_bytes()))
    print(canonical_json(result))
    raise SystemExit(0 if result["status"] == "completed" else 1)
