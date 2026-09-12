"""Generate real research artifacts using authored, deterministic software fixtures."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import httpx

from research_harness.backend import Backend
from research_harness.config import SourceSpec
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.evaluation.benchmark import (
    CaseRun,
    LoadedBenchmark,
    RunControls,
    RunManifest,
    compare_runs,
    load_benchmark,
    local_path,
)
from research_harness.services.research import ResearchService
from research_harness.util import canonical_json, digest, write_json


def fixture_response_bytes(response: dict) -> bytes:
    """Preserve real binary documents without ambiguous or lossy fixture encoding."""
    if ("body" in response) == ("body_base64" in response):
        raise ValueError("Fixture response needs exactly one of body or body_base64")
    if "body_base64" in response:
        if not isinstance(response["body_base64"], str):
            raise ValueError("Fixture body_base64 must be a string")
        return base64.b64decode(response["body_base64"], validate=True)
    body = response["body"]
    return (body if isinstance(body, str) else canonical_json(body)).encode("utf-8")


class FixtureProvider:
    name = "authored-development-fixtures"

    def __init__(self, results: list[dict[str, Any]]):
        self.results = results

    def search(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        return {
            "results": self.results,
            "provider_response": {
                "fixture": True,
                "query": query,
                "filters": filters,
                "results": self.results,
            },
            "provider_usage": {"fixture_searches": 1},
        }


def backend_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = [
        "config.py",
        "engine.py",
        "store.py",
        "registry.py",
        "connectors.py",
        "http.py",
        "services/research.py",
        "services/proposals.py",
    ]
    return digest(canonical_json({path: digest((root / path).read_bytes()) for path in paths}))


def run_fixture_arm(
    benchmark: LoadedBenchmark,
    output: Path,
    *,
    policy: Literal["authored-selection", "first-listed-source"],
    backend_sha256: str | None = None,
) -> Path:
    """Exercise the domain service, without a model or the Omnigent execution loop."""
    if benchmark.manifest.split != "development":
        raise ValueError("The public fixture demonstration only accepts development cases")
    if policy not in {"authored-selection", "first-listed-source"}:
        raise ValueError("Unknown fixture policy")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    controls = RunControls(
        execution="fixture",
        runtime="research-service-fixture",
        model="no-model-fixture",
        model_settings={},
        provider=FixtureProvider.name,
        provider_settings={"network": "disabled", "source": "authored HTTP fixtures"},
        budgets={"search": 8, "inspection": 16, "probe": 12, "deadline_seconds": 600},
        fixture_sha256=benchmark.fixture_sha256,
        strategy_sha256=digest(
            canonical_json(
                {"policy": policy, "implementation": digest(Path(__file__).read_bytes())}
            )
        ),
        backend_sha256=backend_sha256 or backend_fingerprint(),
    )
    runs = []
    for case in benchmark.cases.values():
        fixture = json.loads(local_path(benchmark.path.parent, case.fixtures.path).read_bytes())
        responses = {response["url"]: response for response in fixture["responses"]}
        if len(responses) != len(fixture["responses"]):
            raise ValueError("Fixture response URLs must be unique")

        def handler(request: httpx.Request, responses=responses) -> httpx.Response:
            response = responses.get(str(request.url))
            if response is None:
                raise RuntimeError(
                    "No authored fixture for requested URL; network access is disabled"
                )
            return httpx.Response(
                response.get("status", 200),
                headers=response.get("headers", {}),
                content=fixture_response_bytes(response),
            )

        started = perf_counter()
        case_output = output / "cases" / case.id
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            service = ResearchService(
                case_output,
                backend=Backend.local(output / "backend"),
                http_client=client,
                public_only=False,
            )
            try:
                service.begin(
                    case.brief,
                    model=controls.model,
                    runtime={
                        "adapter": controls.runtime,
                        "strategy_hash": controls.strategy_sha256,
                    },
                    limits={
                        key: controls.budgets[key] for key in ("search", "inspection", "probe")
                    },
                    deadline_seconds=controls.budgets["deadline_seconds"],
                )
                service.search(
                    case.brief,
                    provider=FixtureProvider(fixture["search_results"]),
                    operation_id="search-1",
                )
                chosen = (
                    case.fixture_plan.source_ids
                    if policy == "authored-selection"
                    else [source.id for source in case.sources[:1]]
                )
                candidates = []
                for source in case.sources:
                    if source.id not in chosen:
                        continue
                    proof = service.probe(source, operation_id=f"probe-{source.id}")
                    passed = proof["status"] == "verified_sample"
                    candidates.append(
                        Candidate(
                            name=source.name,
                            purpose="Collect the requested research evidence",
                            covers=["research"],
                            status="ready" if passed else "unavailable",
                            evidence_urls=[source.url],
                            source=source if passed else None,
                            probe_id=proof["probe_id"] if passed else None,
                            freshness_assessment="Authored fixture; no live freshness evidence",
                            historical_coverage="Only the fixture's sampled content is demonstrated",
                            access_notes="Synthetic HTTP response; access rights and availability are unverified",
                            limitations=[
                                "Deterministic software fixture, not model output",
                                "A sample does not prove complete ingestion",
                            ],
                        )
                    )
                unsupported_choices = (
                    case.fixture_plan.unsupported if policy == "authored-selection" else []
                )
                for index, unsupported in enumerate(unsupported_choices):
                    if unsupported.status == "needs_connector":
                        service.inspect(unsupported.url, operation_id=f"inspect-gap-{index}")
                    else:
                        # Failed probes retain captured HTTP errors as scoped evidence.
                        # Inspection raises on HTTP errors and cannot export such a receipt.
                        service.probe(
                            SourceSpec(
                                id=f"gap-{index}",
                                name=unsupported.name,
                                connector="html",
                                url=unsupported.url,
                            ),
                            operation_id=f"probe-gap-{index}",
                        )
                    candidates.append(
                        Candidate(
                            name=unsupported.name,
                            purpose="Record the unresolved part of the brief",
                            covers=["research"],
                            status=unsupported.status,
                            evidence_urls=[unsupported.url],
                            source=None,
                            probe_id=None,
                            freshness_assessment="Not established",
                            historical_coverage="Not established",
                            access_notes="Authored unsupported-source fixture",
                            limitations=[unsupported.limitation],
                        )
                    )
                proposal = ProposalDraft(
                    name=case.id,
                    title=case.title,
                    research_question=case.brief,
                    needs=[DataNeed(id="research", description=case.brief, required=True)],
                    candidates=candidates,
                    open_questions=case.manual_checks,
                )
                service.submit_proposal(proposal, operation_id="submit-1")
                runs.append(
                    CaseRun(
                        case_id=case.id,
                        status="completed",
                        artifacts=f"cases/{case.id}",
                        elapsed_seconds=perf_counter() - started,
                    )
                )
            except Exception as exc:
                service.finish_failure(exc, usage={})
                runs.append(
                    CaseRun(
                        case_id=case.id,
                        status="failed",
                        artifacts=f"cases/{case.id}",
                        error=f"{type(exc).__name__}: {exc}",
                        elapsed_seconds=perf_counter() - started,
                    )
                )
    manifest = RunManifest(
        name=policy, benchmark_sha256=benchmark.sha256, controls=controls, runs=runs
    )
    path = output / "run.json"
    write_json(path, manifest.model_dump(mode="json"))
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", type=Path)
    parser.add_argument(
        "output", type=Path, help="New output directory; existing runs are preserved"
    )
    args = parser.parse_args(argv)
    try:
        benchmark = load_benchmark(args.benchmark)
        if args.output.exists():
            raise ValueError("Choose a new fixture comparison output directory")
        args.output.mkdir(parents=True)
        backend_hash = backend_fingerprint()
        paths = [
            run_fixture_arm(
                benchmark, args.output / policy, policy=policy, backend_sha256=backend_hash
            )
            for policy in ("authored-selection", "first-listed-source")
        ]
        report = compare_runs(args.benchmark, paths, axis="strategy")
        write_json(args.output / "comparison.json", report)
        print(
            json.dumps(
                {
                    "execution": "fixture",
                    "report": str((args.output / "comparison.json").resolve()),
                    "arms": [
                        {"name": arm["name"], "summary": arm["summary"]} for arm in report["arms"]
                    ],
                },
                indent=2,
            )
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"Fixture comparison failed: {exc}\n")


if __name__ == "__main__":
    main()
