from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from openai import OpenAIError

from research_harness.config import PipelineSpec, SourceSpec, data_path, load_pipeline
from research_harness.connectors import CONNECTOR_CATALOG
from research_harness.discovery import ProposalDraft, discover
from research_harness.engine import export_dataset, probe_source, replay_run, run_pipeline
from research_harness.store import Store
from research_harness.util import error_message, parse_timestamp, timestamp, utcnow, write_json


def emit(value: Any, *, stderr: bool = False) -> None:
    print(
        json.dumps(value, ensure_ascii=False, indent=None if stderr else 2),
        file=sys.stderr if stderr else sys.stdout,
        flush=True,
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="rh",
        description="Discover sources, propose pipelines, and collect traceable research data.",
    )
    sub = root.add_subparsers(dest="command", required=True)
    discovery = sub.add_parser(
        "discover", help="An agent discovers, probes, and proposes sources for a research question"
    )
    discovery.add_argument("question", nargs="?")
    discovery.add_argument("--brief", type=Path, help="Read the research brief from a UTF-8 file")
    discovery.add_argument("--out", type=Path, required=True)
    discovery.add_argument("--model")
    discovery.add_argument(
        "--max-rounds", type=int, default=16, choices=range(1, 31), metavar="1..30"
    )
    validate = sub.add_parser("validate", help="Validate a pipeline definition without fetching")
    validate.add_argument("pipeline", type=Path)
    probe = sub.add_parser("probe", help="Fetch and structurally validate one source sample")
    probe.add_argument("source", type=Path, help="A SourceSpec JSON file")
    probe.add_argument("--data-dir", type=Path, default=Path(".researchharness/probes"))
    run = sub.add_parser(
        "run", help="Collect each source; publish only complete, valid source snapshots"
    )
    run.add_argument("pipeline", type=Path)
    run.add_argument("--source", help="Run only this source id")
    run.add_argument(
        "--due", action="store_true", help="Skip sources whose polling interval has not elapsed"
    )
    status = sub.add_parser("status", help="Show source health, recent runs, or one run's details")
    status.add_argument("pipeline", type=Path)
    status.add_argument("--run-id")
    export = sub.add_parser(
        "export", help="Export latest verified observations available at a cutoff"
    )
    export.add_argument("pipeline", type=Path)
    export.add_argument("--as-of", help="ISO timestamp with timezone; defaults to now")
    export.add_argument("--kind", choices=["market", "orderbook", "document", "observation"])
    export.add_argument("--out", type=Path, required=True)
    replay = sub.add_parser(
        "replay", help="Normalize a saved run offline without publishing or changing checkpoints"
    )
    replay.add_argument("pipeline", type=Path)
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--out", type=Path, required=True)
    schema = sub.add_parser("schema", help="Print a machine-readable configuration schema")
    schema.add_argument("kind", choices=["source", "pipeline", "proposal"])
    sub.add_parser("catalog", help="List supported connector capabilities")
    return root


def execute(args: argparse.Namespace) -> int:
    if args.command == "catalog":
        emit(CONNECTOR_CATALOG)
        return 0
    if args.command == "schema":
        emit(
            {"source": SourceSpec, "pipeline": PipelineSpec, "proposal": ProposalDraft}[
                args.kind
            ].model_json_schema()
        )
        return 0
    if args.command == "discover":
        if bool(args.question) == bool(args.brief):
            raise ValueError("Supply either a question or --brief, but not both")
        brief = args.brief.read_text(encoding="utf-8") if args.brief else args.question
        if not brief.strip():
            raise ValueError("The research brief is empty")
        emit(
            discover(
                brief,
                args.out,
                args.model,
                args.max_rounds,
                progress=lambda event: emit(event, stderr=True),
            )
        )
        return 0
    if args.command == "probe":
        source = SourceSpec.model_validate_json(args.source.read_text())
        with Store(args.data_dir) as store:
            report = probe_source(source, store)
            emit(report)
            return 0 if report["status"] == "verified_sample" else 1
    spec = load_pipeline(args.pipeline)
    if args.command == "validate":
        emit(
            {
                "status": "valid",
                "pipeline": spec.name,
                "fingerprint": spec.fingerprint(),
                "sources": [
                    {"id": source.id, "connector": source.connector, "enabled": source.enabled}
                    for source in spec.sources
                ],
                "data_dir": str(data_path(spec, args.pipeline)),
                "network_requests": 0,
            }
        )
        return 0
    with Store(data_path(spec, args.pipeline)) as store:
        if args.command == "run":
            result = run_pipeline(
                spec,
                store,
                only_source=args.source,
                due_only=args.due,
                on_progress=lambda event: emit(event, stderr=True),
            )
            emit(result)
            return 0 if result["status"] == "succeeded" else 1
        if args.command == "status":
            if args.run_id:
                emit(
                    {
                        **store.run_details(args.run_id),
                        "quarantine": store.quarantine_for_run(args.run_id),
                    }
                )
            else:
                now = utcnow()
                health = store.health(spec, timestamp(now))
                for source in health:
                    source["stale"] = (
                        source["last_success_at"] is None
                        or (now - parse_timestamp(source["last_success_at"])).total_seconds()
                        > source["poll_interval_seconds"]
                    )
                runs = [
                    dict(row)
                    for row in store.db.execute(
                        "SELECT id,started_at,finished_at,status FROM runs WHERE pipeline=? ORDER BY started_at DESC LIMIT 10",
                        (spec.name,),
                    )
                ]
                emit(
                    {
                        "pipeline": spec.name,
                        "as_of": timestamp(now),
                        "sources": health,
                        "recent_runs": runs,
                    }
                )
            return 0
        if args.command == "export":
            cutoff = parse_timestamp(args.as_of) if args.as_of else utcnow()
            emit(export_dataset(spec, store, cutoff, args.out, args.kind))
            return 0
        if args.command == "replay":
            replay = replay_run(store, args.run_id)
            write_json(args.out, replay)
            emit(
                {
                    "output": str(args.out),
                    "run_id": args.run_id,
                    "records": len(replay["records"]),
                    "issues": replay["issues"],
                    "network_requests": 0,
                    "published": False,
                }
            )
            return 0 if not replay["issues"] else 1
    raise ValueError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return execute(args)
    except (ValueError, OSError, RuntimeError, OpenAIError, sqlite3.Error) as exc:
        emit(
            {"status": "error", "error": f"{type(exc).__name__}: {error_message(exc)}"}, stderr=True
        )
        return 1
    except KeyboardInterrupt:
        emit(
            {
                "status": "interrupted",
                "message": "Run interrupted; incomplete source data was not published.",
            },
            stderr=True,
        )
        return 130
