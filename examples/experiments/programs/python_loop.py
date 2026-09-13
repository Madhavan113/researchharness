"""Small editable coding agent. This example is not Harbor's Terminus-2 baseline.

The controller starts this file inside the task container. stdout is reserved
for newline-delimited model requests; diagnostics belong on stderr. No SDK,
network access or provider credentials are needed inside the container.
"""

import json
import subprocess
import sys


def model_request(history):
    print(
        json.dumps(
            {
                "instructions": "Solve the task using shell to inspect, implement and test. "
                "Finish with a concise final message when done.",
                "input": history,
                "tools": [
                    {
                        "type": "function",
                        "name": "shell",
                        "description": "Run a shell command.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                            "additionalProperties": False,
                        },
                    }
                ],
            }
        ),
        flush=True,
    )
    return json.loads(sys.stdin.readline())


def main():
    task = json.loads(sys.stdin.readline())
    assert task["protocol"] == 1
    history = [{"role": "user", "content": task["instruction"]}]
    for _ in range(10):
        response = model_request(history)
        history.extend(response["output"])
        calls = [item for item in response["output"] if item["type"] == "function_call"]
        if not calls:
            if response.get("status") != "completed":
                raise RuntimeError("Model response did not complete")
            return
        for call in calls:
            if call["name"] != "shell":
                raise ValueError("Unknown local tool")
            command = json.loads(call["arguments"])["command"]
            print(f"shell: {command}", file=sys.stderr, flush=True)
            try:
                result = subprocess.run(
                    command, shell=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=60
                )
                output = {
                    "exit_code": result.returncode,
                    "stdout": result.stdout[:65536].decode(errors="replace"),
                    "stderr": result.stderr[:65536].decode(errors="replace"),
                }
            except subprocess.TimeoutExpired:
                output = {"error": "Command exceeded 60 seconds"}
            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(output),
                }
            )
    raise RuntimeError("Example agent exhausted its ten model steps")


if __name__ == "__main__":
    main()
