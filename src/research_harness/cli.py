from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from openai import OpenAIError

from research_harness import registry
from research_harness.backend import Backend, backend_errors
from research_harness.config import PipelineSpec, SourceSpec, data_path, load_pipeline
from research_harness.connectors import CONNECTOR_CATALOG
from research_harness.discovery import ProposalDraft, discover
from research_harness.engine import export_dataset, probe_source, replay_run, run_pipeline
from research_harness.util import error_message, parse_timestamp, timestamp, utcnow, write_json

PIPELINE_HELP = "A pipeline JSON file, or the id (or unique id prefix) of a registered pipeline"


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
    experiment = sub.add_parser(
        "experiment",
        help="Prepare research experiments and check their benchmarks for human review",
    )
    experiment_sub = experiment.add_subparsers(dest="experiment_command", required=True)
    prepare_experiment = experiment_sub.add_parser(
        "prepare", help="Snapshot a proposal; execute nothing"
    )
    prepare_experiment.add_argument("manifest", type=Path)
    prepare_experiment.add_argument("--checkout", type=Path, required=True)
    prepare_experiment.add_argument("--out", type=Path, required=True)
    check_experiment = experiment_sub.add_parser(
        "check", help="Run Harbor oracle/no-op benchmark controls"
    )
    check_experiment.add_argument("prepared", type=Path)
    check_experiment.add_argument("--out", type=Path, required=True)
    check_experiment.add_argument("--harbor", type=Path, required=True)
    check_experiment.add_argument("--timeout", type=int, default=1800)
    experiment_status = experiment_sub.add_parser(
        "status", help="Inspect an existing check or execution without rerunning"
    )
    experiment_status.add_argument("output", type=Path)
    review = experiment_sub.add_parser(
        "review", help="Inspect a benchmark package, or record an operator decision"
    )
    review.add_argument("prepared", type=Path)
    review.add_argument("--check", type=Path, required=True)
    review.add_argument(
        "--store", type=Path, required=True, help="Operator curator SQLite database"
    )
    review.add_argument("--decision", choices=["accept", "reject"])
    review.add_argument("--subject", help="Exact subject_sha256 from inspection")
    review.add_argument("--after", help="Exact review head from inspection, or 'none'")
    review.add_argument("--reason", help="Why this benchmark package is accepted or rejected")
    reviews = experiment_sub.add_parser(
        "reviews", help="Read the retained curator decision history"
    )
    reviews.add_argument("experiment_id")
    reviews.add_argument("--store", type=Path, required=True)
    withdraw = experiment_sub.add_parser("withdraw", help="Withdraw a current benchmark acceptance")
    withdraw.add_argument("experiment_id")
    withdraw.add_argument("--store", type=Path, required=True)
    withdraw.add_argument("--after", required=True)
    withdraw.add_argument("--reason", required=True)
    discovery = sub.add_parser(
        "discover", help="An agent discovers, probes, and proposes sources for a research question"
    )
    discovery.add_argument("question", nargs="?")
    discovery.add_argument("--brief", type=Path, help="Read the research brief from a UTF-8 file")
    discovery.add_argument("--out", type=Path, required=True)
    discovery.add_argument("--model")
    discovery.add_argument(
        "--search-provider",
        choices=["native", "mcp"],
        default="native",
        help="Native web search (default), or the same MCP provider wrapper used by Omnigent",
    )
    discovery.add_argument(
        "--search-endpoint",
        help="MCP search endpoint; requires --search-provider mcp (default: Keenable)",
    )
    discovery.add_argument(
        "--max-rounds",
        type=int,
        choices=range(1, 31),
        metavar="1..30",
        help="Model round limit (default: 16); must agree with --settings when supplied",
    )
    discovery.add_argument(
        "--settings", type=Path, help="Read explicit shared discovery settings from a JSON file"
    )
    discovery.add_argument(
        "--instructions", type=Path, help="Read shared semantic instructions from a UTF-8 file"
    )
    discovery.add_argument(
        "--strategy",
        type=Path,
        help="Frozen code-strategy manifest; requires --search-provider mcp",
    )
    mcp = sub.add_parser("mcp", help="Expose verified research operations to an agent runtime")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    serve = mcp_sub.add_parser("serve", help="Run a local MCP server over stdio")
    serve.add_argument(
        "--out", type=Path, required=True, help="This discovery's artifact directory"
    )
    serve.add_argument("--discovery", help="Resume this service-issued discovery id")
    serve.add_argument(
        "--model", help="Host model (new case: gpt-5.4-mini; resume: the saved model)"
    )
    serve.add_argument("--session-id", help="Host session identifier recorded with the case")
    serve.add_argument("--search-endpoint", default="https://api.keenable.ai/mcp")
    serve.add_argument(
        "--strategy", type=Path, help="Code-strategy manifest bound to this discovery"
    )
    strategy = sub.add_parser("strategy", help="Inspect or recover an isolated strategy session")
    strategy_sub = strategy.add_subparsers(dest="strategy_command", required=True)
    for command in ("status", "recover"):
        action = strategy_sub.add_parser(command)
        action.add_argument(
            "--out", type=Path, required=True, help="Existing strategy session directory"
        )
    validate = sub.add_parser("validate", help="Validate a pipeline definition without fetching")
    validate.add_argument("pipeline", help=PIPELINE_HELP)
    probe = sub.add_parser("probe", help="Fetch and structurally validate one source sample")
    probe.add_argument("source", type=Path, help="A SourceSpec JSON file")
    probe.add_argument("--data-dir", type=Path, default=Path(".researchharness/probes"))
    run = sub.add_parser(
        "run", help="Collect each source; publish only complete, valid source snapshots"
    )
    run.add_argument("pipeline", help=PIPELINE_HELP)
    run.add_argument("--source", help="Run only this source id")
    run.add_argument(
        "--due", action="store_true", help="Skip sources whose polling interval has not elapsed"
    )
    status = sub.add_parser("status", help="Show source health, recent runs, or one run's details")
    status.add_argument("pipeline", help=PIPELINE_HELP)
    status.add_argument("--run-id")
    status.add_argument(
        "--legacy-unscoped",
        action="store_true",
        help="Read pre-namespace data for this registered pipeline; ownership may be ambiguous",
    )
    export = sub.add_parser(
        "export", help="Export latest verified observations available at a cutoff"
    )
    export.add_argument("pipeline", help=PIPELINE_HELP)
    export.add_argument("--as-of", help="ISO timestamp with timezone; defaults to now")
    export.add_argument("--kind", choices=["market", "orderbook", "document", "observation"])
    export.add_argument("--out", type=Path, required=True)
    export.add_argument(
        "--legacy-unscoped", action="store_true", help="Read pre-namespace registered data"
    )
    replay = sub.add_parser(
        "replay", help="Normalize a saved run offline without publishing or changing checkpoints"
    )
    replay.add_argument("pipeline", help=PIPELINE_HELP)
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--out", type=Path, required=True)
    replay.add_argument(
        "--legacy-unscoped", action="store_true", help="Read pre-namespace registered data"
    )
    schema = sub.add_parser("schema", help="Print a machine-readable configuration schema")
    schema.add_argument("kind", choices=["source", "pipeline", "proposal"])
    sub.add_parser("catalog", help="List supported connector capabilities")

    backend = sub.add_parser("backend", help="Inspect, initialize, or check the storage backend")
    backend_sub = backend.add_subparsers(dest="backend_command", required=True)
    backend_sub.add_parser("info", help="Show the configured backend without connecting")
    backend_sub.add_parser("init", help="Create or upgrade the schema and verify the bucket")
    backend_sub.add_parser("check", help="Connect, report counts, and round-trip a blob")

    questions = sub.add_parser("questions", help="Research questions in the registry")
    questions_sub = questions.add_subparsers(dest="questions_command", required=True)
    add = questions_sub.add_parser("add", help="Register a research question from text or a brief")
    add.add_argument("question", nargs="?")
    add.add_argument("--brief", type=Path, help="Read the research brief from a UTF-8 file")
    add.add_argument("--title")
    questions_sub.add_parser("list", help="List questions with discovery and pipeline counts")
    show = questions_sub.add_parser(
        "show", help="Show a question's discoveries, proposals, and candidate pipelines"
    )
    show.add_argument("question_id")

    pipelines = sub.add_parser("pipelines", help="Candidate pipeline definitions in the registry")
    pipelines_sub = pipelines.add_subparsers(dest="pipelines_command", required=True)
    plist = pipelines_sub.add_parser("list", help="List registered pipeline versions")
    plist.add_argument("--question", help="Question id or prefix")
    plist.add_argument("--status", choices=registry.PIPELINE_STATUSES)
    pshow = pipelines_sub.add_parser("show", help="Show one definition with its history")
    pshow.add_argument("pipeline_id")
    preg = pipelines_sub.add_parser(
        "register", help="Register a pipeline JSON file as a candidate for a question"
    )
    preg.add_argument("pipeline", type=Path)
    preg.add_argument("--question", required=True, help="Question id or prefix")
    preg.add_argument("--label")
    preg.add_argument("--notes")
    for name, help_text in [
        ("adopt", "Adopt a definition; the question's previously adopted one becomes superseded"),
        ("retire", "Retire a definition"),
        ("reopen", "Return a retired or superseded definition to proposed"),
    ]:
        command = pipelines_sub.add_parser(name, help=help_text)
        command.add_argument("pipeline_id")
        command.add_argument("--note")
    phist = pipelines_sub.add_parser("history", help="Show a definition's status events")
    phist.add_argument("pipeline_id")
    pexport = pipelines_sub.add_parser(
        "export", help="Write a registered definition to a pipeline JSON file"
    )
    pexport.add_argument("pipeline_id")
    pexport.add_argument("--out", type=Path, required=True)
    return root


def read_brief(args: argparse.Namespace) -> str:
    if bool(args.question) == bool(args.brief):
        raise ValueError("Supply either a question or --brief, but not both")
    brief = args.brief.read_text(encoding="utf-8") if args.brief else args.question
    if not brief.strip():
        raise ValueError("The research brief is empty")
    return brief


def resolve_pipeline(
    backend: Backend, value: str
) -> tuple[PipelineSpec, Path | None, dict[str, Any] | None]:
    """A pipeline argument is a JSON file path or a registry id."""
    path = Path(value)
    if path.is_file():
        return load_pipeline(path), path, None
    if path.suffix == ".json" or "/" in value:
        raise FileNotFoundError(f"Pipeline file not found: {value}")
    with backend.open_registry() as store:
        row, spec = registry.pipeline_spec(store, value)
    return spec, None, row


def _strategy_session(manifest: Path, output: Path, *, create: bool = True):
    from research_harness.strategies.config import StrategyBundle
    from research_harness.strategies.session import StrategySession

    return StrategySession(output, StrategyBundle.load(manifest), create=create)


def registry_command(args: argparse.Namespace, backend: Backend) -> int:
    with backend.open_registry() as store:
        if args.command == "questions":
            if args.questions_command == "add":
                emit(registry.register_question(store, read_brief(args), title=args.title))
            elif args.questions_command == "list":
                emit(registry.list_questions(store))
            else:
                result = registry.question_overview(store, args.question_id)
                result["pipelines"] = backend.describe_pipelines(store, result["pipelines"])
                emit(result)
            return 0
        command = args.pipelines_command
        if command == "list":
            emit(
                backend.describe_pipelines(
                    store,
                    registry.list_pipelines(store, question_id=args.question, status=args.status),
                )
            )
        elif command == "show":
            emit(
                {
                    **registry.get_pipeline(store, args.pipeline_id),
                    "history": registry.pipeline_history(store, args.pipeline_id),
                }
            )
        elif command == "register":
            emit(
                registry.register_pipeline(
                    store,
                    args.question,
                    load_pipeline(args.pipeline),
                    origin="manual",
                    label=args.label,
                    notes=args.notes,
                )
            )
        elif command == "adopt":
            emit(registry.adopt_pipeline(store, args.pipeline_id, note=args.note))
        elif command == "retire":
            emit(registry.retire_pipeline(store, args.pipeline_id, note=args.note))
        elif command == "reopen":
            emit(registry.set_status(store, args.pipeline_id, "proposed", note=args.note))
        elif command == "history":
            emit(registry.pipeline_history(store, args.pipeline_id))
        else:
            row, spec = registry.pipeline_spec(store, args.pipeline_id)
            write_json(args.out, spec.model_dump(mode="json"))
            emit(
                {
                    "output": str(args.out),
                    "pipeline_version_id": row["id"],
                    "fingerprint": row["fingerprint"],
                    "status": row["status"],
                }
            )
    return 0


def execute(args: argparse.Namespace) -> int:
    if args.command == "experiment":
        from research_harness.experiments.checks import check, status
        from research_harness.experiments.package import prepare

        if args.experiment_command == "prepare":
            emit(prepare(args.manifest, args.checkout, args.out))
            return 0
        if args.experiment_command in {"review", "reviews", "withdraw"}:
            from research_harness.experiments.curation import CuratorStore

            curator = CuratorStore(args.store)
            if args.experiment_command == "reviews":
                emit(
                    {
                        "experiment_id": args.experiment_id,
                        "reviews": curator.history(args.experiment_id),
                    }
                )
            elif args.experiment_command == "withdraw":
                emit(curator.withdraw(args.experiment_id, after=args.after, reason=args.reason))
            elif args.decision:
                if not all((args.subject, args.after, args.reason)):
                    raise ValueError("A decision requires --subject, --after and --reason")
                emit(
                    curator.decide(
                        args.prepared,
                        args.check,
                        decision=args.decision,
                        subject=args.subject,
                        after=args.after,
                        reason=args.reason,
                    )
                )
            else:
                if any((args.subject, args.after, args.reason)):
                    raise ValueError("Decision arguments require --decision")
                emit(curator.inspect(args.prepared, args.check))
            return 0
        if args.experiment_command == "check":
            result = check(args.prepared, args.out, args.harbor, timeout=args.timeout)
        else:
            if (args.output / "execution.json").exists():
                from research_harness.experiments.execution import status as execution_status

                result = execution_status(args.output)
            else:
                result = status(args.output)
        emit(result)
        return 0 if result["status"] in {"passed", "completed"} else 1
    if args.command == "mcp":
        try:
            from research_harness.mcp.server import create_server
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("mcp"):
                raise ValueError("Install the MCP extra: uv sync --extra mcp") from exc
            raise
        from research_harness.services.research import ResearchService
        from research_harness.services.search import McpSearchProvider

        service = ResearchService(args.out, backend=Backend.from_env())
        discovery_id = args.discovery
        context_path = args.out / "context.json"
        if discovery_id is None and context_path.is_file():
            discovery_id = json.loads(context_path.read_text())["discovery_id"]
        if discovery_id:
            service.resume(discovery_id)
        runtime = {"model": args.model, "adapter": "mcp", "session_id": args.session_id}
        server = create_server(
            service,
            search_provider=McpSearchProvider(args.search_endpoint),
            runtime=runtime,
            **(
                {
                    "strategy": _strategy_session(
                        args.strategy, args.out.resolve() / "strategy", create=not discovery_id
                    )
                }
                if args.strategy
                else {}
            ),
        )
        server.run(transport="stdio")
        return 0
    if args.command == "strategy":
        from research_harness.strategies.session import StrategySession

        session = StrategySession.open(args.out.resolve())
        emit(session.recover() if args.strategy_command == "recover" else session.status())
        return 0
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
        from research_harness.execution import DiscoverySettings

        emit(
            discover(
                read_brief(args),
                args.out,
                args.model,
                args.max_rounds,
                progress=lambda event: emit(event, stderr=True),
                search_provider=args.search_provider,
                search_endpoint=args.search_endpoint,
                settings=DiscoverySettings.model_validate_json(
                    args.settings.read_text(encoding="utf-8")
                )
                if args.settings
                else None,
                instructions=args.instructions.read_text(encoding="utf-8")
                if args.instructions
                else None,
                **(
                    {
                        "strategy": _strategy_session(
                            args.strategy,
                            args.out.resolve() / "strategy",
                            create=not (args.out / "context.json").exists(),
                        )
                    }
                    if args.strategy
                    else {}
                ),
            )
        )
        return 0
    backend = Backend.from_env()
    if args.command == "backend":
        if args.backend_command == "info":
            emit(backend.settings.describe())
        elif args.backend_command == "init":
            with backend.open_registry() as store:
                report = store.describe()
            if backend.mode == "postgres":
                report["bucket"] = backend.ensure_bucket()
            emit({"status": "initialized", **report})
        else:
            emit(backend.check())
        return 0
    if args.command in {"questions", "pipelines"}:
        return registry_command(args, backend)
    if args.command == "probe":
        source = SourceSpec.model_validate_json(args.source.read_text())
        with backend.open_store(args.data_dir) as store:
            report = probe_source(source, store)
            emit(report)
            return 0 if report["status"] == "verified_sample" else 1
    spec, config_path, registered = resolve_pipeline(backend, args.pipeline)
    legacy = bool(getattr(args, "legacy_unscoped", False))
    if legacy and registered is None:
        raise ValueError("--legacy-unscoped requires a registered pipeline id")
    legacy_data = backend.legacy_pipeline_data(spec) if registered else None
    if args.command == "validate":
        if backend.mode == "local":
            storage = str(
                data_path(spec, config_path)
                if config_path
                else backend.settings.local_root
                / "pipelines"
                / registered["question_id"]
                / spec.name
            )
        else:
            storage = backend.settings.describe()["database_url"]
        emit(
            {
                "status": "valid",
                "pipeline": spec.name,
                "fingerprint": spec.fingerprint(),
                "sources": [
                    {"id": source.id, "connector": source.connector, "enabled": source.enabled}
                    for source in spec.sources
                ],
                "backend": backend.mode,
                "data_dir": storage,
                "registry": {
                    "pipeline_version_id": registered["id"],
                    "status": registered["status"],
                }
                if registered
                else None,
                "network_requests": 0,
                "legacy_unscoped_data": legacy_data,
            }
        )
        return 0
    with backend.pipeline_store(
        spec,
        config_path,
        question_id=registered["question_id"] if registered and not legacy else None,
    ) as store:
        if run_id := getattr(args, "run_id", None):
            if store.run_details(run_id)["pipeline"] != spec.name:
                raise ValueError("Run does not belong to this pipeline dataset")
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
                emit(
                    {
                        "pipeline": spec.name,
                        "as_of": timestamp(now),
                        "backend": store.describe(),
                        "sources": health,
                        "recent_runs": store.recent_runs(spec.name),
                        "legacy_unscoped_data": legacy_data,
                        "reading_legacy_unscoped": legacy,
                    }
                )
            return 0
        if args.command == "export":
            cutoff = parse_timestamp(args.as_of) if args.as_of else utcnow()
            report = export_dataset(spec, store, cutoff, args.out, args.kind)
            if registered:
                report.update(
                    legacy_unscoped_data=legacy_data,
                    reading_legacy_unscoped=legacy,
                )
                write_json(
                    Path(report["manifest"]),
                    {
                        key: value
                        for key, value in report.items()
                        if key not in {"output", "manifest"}
                    },
                )
            emit(report)
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
    except (
        ValueError,
        OSError,
        RuntimeError,
        OpenAIError,
        sqlite3.Error,
        *backend_errors(),
    ) as exc:
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
