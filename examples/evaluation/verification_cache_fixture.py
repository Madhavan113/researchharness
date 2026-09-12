"""Measure repeated feedback verification without model requests or Docker execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from research_harness.optimization import workspace as module
from research_harness.optimization.workspace import ProposalWorkspace, WorkspaceConfig
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.util import write_json

IMAGE = "python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--files", type=int, default=16)
    parser.add_argument("--file-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--repetitions", type=int, default=8)
    args = parser.parse_args()
    if not (
        1 <= args.files <= 512
        and 1 <= args.file_bytes <= 4 * 1024 * 1024
        and args.files * args.file_bytes <= 512 * 1024 * 1024
        and 1 <= args.repetitions <= 128
    ):
        parser.error("Use 1–512 files, at most 4 MiB/file and 512 MiB total, and 1–128 repeats")
    output = args.out.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    feedback = output / "feedback"
    feedback.mkdir()
    for index in range(args.files):
        path = feedback / f"artifact-{index:05d}.bin"
        path.write_bytes(hashlib.shake_256(str(index).encode()).digest(args.file_bytes))
        path.chmod(0o600)
    config = WorkspaceConfig(sandbox=SandboxConfig(image=IMAGE))
    workspace = ProposalWorkspace(feedback, output / "workspace-run", config)
    paths = {
        root / f"artifact-{index:05d}.bin"
        for root in (workspace.feedback_source, workspace.feedback)
        for index in range(args.files)
    }
    opens, observed_bytes = 0, 0
    original_open = os.open

    def counted_open(file, flags, *values, **kwargs):
        nonlocal opens, observed_bytes
        descriptor = original_open(file, flags, *values, **kwargs)
        if isinstance(file, (str, bytes, os.PathLike)) and Path(os.fsdecode(file)) in paths:
            if flags & os.O_ACCMODE == os.O_RDONLY:
                opens += 1
                observed_bytes += os.fstat(descriptor).st_size
        return descriptor

    try:
        # Let host-maintained timestamps settle before measuring immutable reads.
        time.sleep(2.1)
        workspace.call("list_files", {"path": "feedback/", "limit": 1})
        os.open = counted_open
        started = time.perf_counter()
        for _ in range(args.repetitions):
            result = workspace.call("list_files", {"path": "feedback/", "limit": 1})
            assert result["total_files"] == args.files
        elapsed = time.perf_counter() - started
    finally:
        os.open = original_open
        proof = workspace.close()
    assert proof["snapshot_valid"] and proof["quiescent"] and proof["feedback_view_removed"]
    root = Path(__file__).resolve().parents[2]
    sources = [Path(module.__file__), root / "src/research_harness/verification.py"]
    report = {
        "schema_version": 1,
        "fixture_only": True,
        "model_requests": 0,
        "docker_executions": 0,
        "files": args.files,
        "file_bytes": args.file_bytes,
        "repetitions": args.repetitions,
        "bytes_per_full_feedback_check": 2 * args.files * args.file_bytes,
        "observed_artifact_read_opens": opens,
        "observed_artifact_read_bytes": observed_bytes,
        "elapsed_seconds": elapsed,
        "feedback_copy_removed": not workspace.feedback.parent.exists(),
        "python": sys.version,
        "implementation_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
            if path.is_file()
        },
        "fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "Measures repeated list operations on authored, settled feedback files only.",
            "Counts full artifact read opens and their byte sizes; metadata reads remain.",
            "Warm filesystem timing is not live model performance or disk throughput.",
        ],
    }
    write_json(output / "report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
