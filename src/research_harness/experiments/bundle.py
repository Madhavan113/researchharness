"""Build a trusted Omnigent supervisor and one declared worker.

Use inline agents and a fixed connector function: the pinned directory parser
drops max_sessions, and its inline-agent translator drops nested MCP servers.
"""

from pathlib import Path

from research_harness.util import write_json


def tool_policy(*, program: bool = False) -> dict:
    # Omnigent registers additional management/browser tools regardless of the
    # bundle. Install a root session policy before the first message; the pinned
    # runtime inherits it in children and evaluates it before tool dispatch.
    allowed = [
        "sys_session_send",
        "sys_session_list",
        "sys_session_get_history",
        "sys_session_get_info",
        "sys_read_inbox",
        "workspace_run_program" if program else "workspace_execute",
    ]
    import json

    return {
        "name": "experiment_tool_boundary",
        "type": "python",
        "handler": "omnigent.policies.builtins.cel.cel_policy",
        "factory_params": {
            "expression": (
                'event.type == "tool_call" && !(event.data.name in '
                + json.dumps(allowed)
                + ') ? {"result": "DENY"} : {"result": "ALLOW"}'
            ),
            "reason": "experiment_tool_boundary: tool is outside the curated execution interface",
        },
        "enabled": True,
    }


def build_bundle(output: Path, *, name: str, model: str, program: bool = False) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    executor = {"harness": "openai-agents", "model": model, "max_iterations": 30}
    write_json(
        output / "experiment.yaml",
        {
            "name": name,
            "executor": executor,
            "async": True,
            "cancellable": True,
            "skills": "none",
            "spawn": False,
            "prompt": (
                "Delegate the task to the declared worker using sys_session_send. "
                "Pass the full task instruction in args. The worker acts inside the "
                "assigned task container. Summarize its result after it finishes. "
                "Only the external evaluator determines whether the task passed."
            ),
            "tools": {
                "worker": {
                    "type": "agent",
                    "executor": executor,
                    "max_sessions": 1,
                    "prompt": (
                        "Execute the frozen candidate using workspace_run_program once. "
                        "Report its execution receipt accurately. Only the external evaluator "
                        "determines task success."
                        if program
                        else "Complete the task using workspace_execute in your assigned container. "
                        "Inspect and test your work. Report failures accurately. "
                        "Do not fabricate benchmark scores or modify evaluator output."
                    ),
                    "tools": {
                        ("workspace_run_program" if program else "workspace_execute"): {
                            "type": "function",
                            "callable": "research_harness.experiments.worker_tools."
                            + ("run_program" if program else "execute"),
                            "description": "Run the frozen candidate program."
                            if program
                            else "Run a command inside the assigned task container.",
                        }
                    },
                }
            },
        },
    )
    return output
