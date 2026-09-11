"""Authored source/model fixtures for actual strategy-search software acceptance.

These deterministic responses exercise the research tools and runtime. They are
not measurements of model quality, source quality, or paid-provider performance.
The response policy receives only a public brief/URL and returned tool receipts;
benchmark predicates and private case files are never inputs to that policy.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from typing import Literal

from research_harness.config import SourceSpec
from research_harness.discovery_models import Candidate, DataNeed, ProposalDraft
from research_harness.util import digest, write_json

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "search_normal_runtime_fixture", _ROOT / "examples/omnigent/normal_runtime_fixture.py"
)
_NORMAL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_NORMAL)


def _source(url: str) -> SourceSpec:
    return SourceSpec(
        id="feed",
        name="Authored public JSON feed",
        connector="json",
        url=url,
        items_pointer="/items",
        id_pointer="/id",
        published_pointer="/published_at",
        required_pointers=["/title"],
    )


def benchmark(output: Path, *, split: Literal["development", "heldout"]) -> Path:
    """Write one authored case in a fresh package, with disjoint split groupings."""
    from research_harness.evaluation.benchmark import BenchmarkCase, BenchmarkManifest, FileRef

    if split == "development":
        topic, title = "transit-notices", "Transit service notices"
        brief = (
            "Find an official public feed of transit service notices with stable ids "
            "and publication timestamps."
        )
        source = _source("https://transit.search-fixture.example/notices.json")
        item = {
            "id": "transit-notice-001",
            "title": "Authored notice of a published route timetable",
            "published_at": "2026-09-01T08:00:00Z",
        }
    elif split == "heldout":
        topic, title = "watershed-bulletins", "Watershed monitoring bulletins"
        brief = (
            "Find an official public feed of watershed monitoring bulletins with stable ids "
            "and publication timestamps."
        )
        source = _source("https://watershed.final-fixture.example/bulletins.json")
        item = {
            "id": "watershed-bulletin-091",
            "title": "Authored bulletin of a river monitoring survey",
            "published_at": "2026-08-29T14:30:00Z",
        }
    else:
        raise ValueError("Choose the development or heldout software-fixture split")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture_path = output / "fixtures/source.json"
    write_json(
        fixture_path,
        {
            "authorship": "Synthetic software acceptance source; no live source was sampled",
            "search_results": [{"url": source.url, "title": title}],
            "responses": [
                {
                    "url": source.url,
                    "status": 200,
                    "headers": {"content-type": "application/json"},
                    "body": {"items": [item]},
                }
            ],
        },
    )
    case = BenchmarkCase(
        id=f"search-{topic}",
        title=title,
        brief=brief,
        topic_group=topic,
        source_families=[f"authored-{topic}"],
        tags=["strategy-search-software-acceptance"],
        review_status="authored",
        review_notes="Software fixture only; independent human case review remains outstanding",
        specification={
            "requirements": [
                {
                    "id": "feed",
                    "weight": 1,
                    "any_of": [
                        {
                            "url": source.url,
                            "connector": "json",
                            "items_pointer": "/items",
                            "id_pointer": "/id",
                            "published_pointer": "/published_at",
                            "required_pointers": ["/title"],
                        }
                    ],
                }
            ]
        },
        fixtures=FileRef(path="fixtures/source.json", sha256=digest(fixture_path.read_bytes())),
        sources=[source],
        fixture_plan={"source_ids": ["feed"], "unsupported": []},
        manual_checks=["Deterministic response policies do not establish live model performance"],
    )
    case_path = output / "cases" / f"{case.id}.json"
    write_json(case_path, case.model_dump(mode="json"))
    manifest = BenchmarkManifest(
        id=f"strategy-search-{split}-software-fixture",
        split=split,
        description="One authored source case for actual runtime software acceptance",
        cases=[
            FileRef(
                path=case_path.relative_to(output).as_posix(), sha256=digest(case_path.read_bytes())
            )
        ],
    )
    manifest_path = output / "manifest.json"
    write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest_path


class ResearchModelFixture:
    """Complete one discovery in seven synthetic requests using actual tool receipts."""

    # Reuse only the normal runtime's tool/SSE serialization helpers. Its global
    # brief, source, response policy, and optional control files are not consulted.
    tool_stream = _NORMAL.ModelFixture.tool_stream
    stream = _NORMAL.ModelFixture.stream

    def __init__(self, brief: str, source_url: str):
        if not isinstance(brief, str) or not brief.strip():
            raise ValueError("A public research brief is required")
        self.brief = brief
        self.source = _source(source_url)
        self.requests = []
        self.responses = []
        self.probe_id = None
        self.saved = None
        self._issued_calls = {}

    def _receipts(self, payload: dict) -> None:
        for item in payload["input"]:
            if item.get("type") != "function_call_output":
                continue
            name = self._issued_calls.get(item.get("call_id"))
            if name not in {"probe_source", "submit_proposal"}:
                continue
            raw = item.get("output")
            if not isinstance(raw, str):
                continue
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                envelope = ast.literal_eval(raw)
            if not isinstance(envelope, dict) or envelope.get("status") != "ok":
                continue
            data = envelope.get("data")
            if not isinstance(data, dict):
                continue
            if name == "probe_source" and data.get("status") == "verified_sample":
                probe_id = data.get("probe_id")
                if isinstance(probe_id, str) and probe_id:
                    if self.probe_id is not None and self.probe_id != probe_id:
                        raise ValueError("The returned probe receipt changed")
                    self.probe_id = probe_id
            if name == "submit_proposal":
                registry = data.get("registry")
                keys = ("question_id", "discovery_id", "proposal_id", "pipeline_version_id")
                if isinstance(registry, dict) and all(
                    isinstance(registry.get(key), str) and registry[key] for key in keys
                ):
                    if self.saved is not None and self.saved != registry:
                        raise ValueError("The returned registry receipt changed")
                    self.saved = deepcopy(registry)

    def _draft(self) -> dict:
        return ProposalDraft(
            name="public-feed",
            title="Public feed for the supplied research brief",
            research_question=self.brief,
            needs=[DataNeed(id="feed", description=self.brief, required=True)],
            candidates=[
                Candidate(
                    name=self.source.name,
                    purpose=self.brief,
                    covers=["feed"],
                    status="ready",
                    evidence_urls=[self.source.url],
                    source=self.source,
                    probe_id=self.probe_id,
                    freshness_assessment="Publication timestamp present in the observed sample",
                    historical_coverage="No historical coverage claim",
                    access_notes="Authored public source fixture",
                    limitations=["Software fixture does not establish live source quality"],
                )
            ],
            open_questions=[],
        ).model_dump(mode="json")

    def respond(self, payload: dict) -> bytes:
        self.requests.append(deepcopy(payload))
        if payload.get("model") != "gpt-5.4-mini":
            raise ValueError("The software fixture requires its recorded model identity")
        index = len(self.responses)
        if index >= 7:
            raise ValueError("The software discovery fixture has already completed")
        self._receipts(payload)
        calls = [
            ("get_research_context", {}),
            ("begin_research", {"brief": self.brief}),
            ("search_sources", {"query": self.brief, "operation_id": "search-1"}),
            ("inspect_source", {"url": self.source.url, "operation_id": "inspect-1"}),
            (
                "probe_source",
                {"source": self.source.model_dump(mode="json"), "operation_id": "probe-1"},
            ),
        ]
        if index < len(calls):
            name, arguments = calls[index]
        elif index == 5:
            if self.probe_id is None:
                raise ValueError("The normal runner did not return a verified probe receipt")
            name = "submit_proposal"
            arguments = {"draft": self._draft(), "operation_id": "submit-1"}
        else:
            if self.saved is None:
                raise ValueError("The normal runner did not return saved domain ids")
            return self.stream(
                {
                    "type": "message",
                    "id": f"msg_{index}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Saved verified pipeline "
                            + json.dumps(self.saved, sort_keys=True),
                            "annotations": [],
                        }
                    ],
                },
                index,
            )
        stream = self.tool_stream(payload, index, name, arguments)
        self._issued_calls[f"call_{index}"] = name
        return stream
