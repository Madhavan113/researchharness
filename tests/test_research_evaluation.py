from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from research_harness.config import PipelineSpec, SourceSpec
from research_harness.engine import probe_source
from research_harness.evaluation import frontier, score, validate_requirements
from research_harness.evaluation.discovery import matches
from research_harness.execution import GatewayBinding
from research_harness.util import digest


@dataclass
class Artifacts:
    path: Path
    source: dict
    specification: dict
    proposal: dict
    probes: list[dict]
    pipeline: dict | None
    events: list[dict] = field(default_factory=list)
    receipts: list[dict] | None = None

    @property
    def candidate(self):
        return self.proposal["proposal"]["candidates"][0]

    def write(self):
        self.path.mkdir(parents=True, exist_ok=True)
        for name, value in (
            ("proposal.json", self.proposal),
            ("probes.json", self.probes),
            ("pipeline.json", self.pipeline),
            ("receipts.json", self.receipts),
        ):
            if value is None:
                (self.path / name).unlink(missing_ok=True)
            else:
                (self.path / name).write_text(json.dumps(value), encoding="utf-8")
        (self.path / "trace.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in self.events), encoding="utf-8"
        )

    def evaluate(self):
        self.write()
        return score(self.path, self.specification)


@pytest.fixture
def artifacts(tmp_path):
    source = SourceSpec(
        id="official",
        name="Official notices",
        url="https://agency.example/notices.json",
        connector="json",
        items_pointer="/items",
        id_pointer="/id",
    )
    source_dict = source.model_dump(mode="json")
    return Artifacts(
        path=tmp_path / "run",
        source=source_dict,
        specification={
            "requirements": [
                {"id": "notices", "any_of": [{"url": source.url, "connector": "json"}]},
                {
                    "id": "market-rules",
                    "any_of": [{"url": "https://exchange.example/rules.json", "connector": "json"}],
                },
            ]
        },
        proposal={
            "status": "proposed",
            "usage": {"input_tokens": 120, "output_tokens": 30},
            "uncovered_required_needs": [],
            "proposal": {
                "needs": [{"id": "notices", "required": True}],
                "candidates": [
                    {
                        "name": "Official",
                        "status": "ready",
                        "source": source_dict,
                        "covers": ["notices", "market-rules"],
                        "probe_id": "probe-1",
                        "evidence_urls": [source.url],
                    }
                ],
            },
        },
        probes=[
            {
                "probe_id": "probe-1",
                "status": "verified_sample",
                "source_fingerprint": source.fingerprint(),
                "record_count": 4,
                "url": source.url,
            }
        ],
        pipeline=PipelineSpec(
            name="policy", description="Policy research", sources=[source]
        ).model_dump(mode="json"),
    )


def test_agent_cannot_remove_independent_needs_or_self_assign_coverage(artifacts):
    result = artifacts.evaluate()
    assert result["status"] == "ok"
    assert result["requirement_recall"] == 0.5
    assert result["quality"] == pytest.approx(2 / 3)
    assert result["source_precision"] == 1
    assert result["coverage"][1] == {"id": "market-rules", "covered": False, "source_ids": []}
    assert result["total_tokens"] == 150


def test_unbound_gateway_usage_cannot_fall_back_to_proposal_tokens(artifacts):
    artifacts.write()
    result = score(
        artifacts.path,
        artifacts.specification,
        gateway_usage=artifacts.path / "gateway/archive.json",
    )
    assert result["quality"] == pytest.approx(2 / 3)
    assert result["token_evidence_source"] == "gateway"
    assert result["gateway_usage"]["status"] == "invalid"
    assert result["total_tokens"] is None
    assert result["completed_token_lower_bound"] is None


def test_workflow_usage_cannot_be_selected_for_discovery(artifacts):
    artifacts.write()
    result = score(
        artifacts.path,
        artifacts.specification,
        gateway_usage=artifacts.path / "gateway/archive.json",
        gateway_binding=GatewayBinding(
            execution_id="a" * 32,
            case_id="case",
            runtime="test",
            phase="workflow",
            task_sha256="b" * 64,
        ),
    )
    assert result["gateway_usage"]["status"] == "invalid"
    assert "discovery phase" in result["gateway_usage"]["errors"][0]
    assert result["total_tokens"] is None


def test_weighted_requirements_and_accepted_alternatives(artifacts):
    artifacts.specification["requirements"][1]["weight"] = 3
    assert artifacts.evaluate()["requirement_recall"] == 0.25
    artifacts.specification["requirements"][1]["any_of"].append(
        {"url": artifacts.source["url"], "connector": "json", "items_pointer": "/items"}
    )
    assert artifacts.evaluate()["quality"] == 1


def test_successful_irrelevant_source_and_exact_configuration_do_not_earn_credit(artifacts):
    artifacts.specification["requirements"][0]["any_of"][0]["items_pointer"] = "/correct"
    result = artifacts.evaluate()
    assert result["status"] == "ok"
    assert result["quality"] == 0
    assert result["source_precision"] == 0


@pytest.mark.parametrize("pointers", [["/filed_at", "/form"], ["/other", "/filed_at", "/form"]])
def test_required_pointers_accept_reordering_and_stricter_supersets(artifacts, pointers):
    artifacts.specification["requirements"] = artifacts.specification["requirements"][:1]
    artifacts.specification["requirements"][0]["any_of"][0]["required_pointers"] = [
        "/form",
        "/filed_at",
    ]
    artifacts.source["required_pointers"] = pointers
    artifacts.pipeline["sources"][0]["required_pointers"] = pointers
    artifacts.probes[0]["source_fingerprint"] = SourceSpec.model_validate(
        artifacts.source
    ).fingerprint()
    assert artifacts.evaluate()["quality"] == 1


def test_pointer_subset_does_not_relax_missing_pointers_or_other_list_fields():
    assert not matches(
        {"required_pointers": ["/form"]}, {"required_pointers": ["/form", "/filed_at"]}
    )
    parameters = [{"name": "q", "value": "first"}, {"name": "q", "value": "second"}]
    assert not matches({"parameters": parameters[::-1]}, {"parameters": parameters})
    assert not matches({"parameters": parameters}, {"parameters": parameters[:1]})
    assert not matches({"max_pages": True}, {"max_pages": 1})


@pytest.mark.parametrize("pointers", ["/form", [None], [12], ["form"], {}])
def test_independent_pointer_predicates_reject_malformed_lists(artifacts, pointers):
    artifacts.specification["requirements"][0]["any_of"][0]["required_pointers"] = pointers
    with pytest.raises(ValueError, match="JSON pointers"):
        artifacts.evaluate()


def gap_artifacts(artifacts, *, status="needs_access", status_code=401, content_type=None):
    url = artifacts.source["url"]
    rule = {"url": url, "status": status, "status_code": status_code}
    if content_type is not None:
        rule["content_type"] = content_type
    artifacts.specification = {
        "requirements": [{"id": "history", "any_of": [], "gap_any_of": [rule]}]
    }
    artifacts.candidate.update(status=status, source=None, probe_id=None)
    artifacts.pipeline = None
    artifacts.probes = []
    artifacts.proposal["registry"] = {"discovery_id": "discovery-1"}
    capture = {
        "capture_id": "capture-1",
        "url": url,
        "status_code": status_code,
        "body_sha256": digest(b"authored response"),
    }
    if content_type is not None:
        capture["content_type"] = content_type
    artifacts.receipts = [{"kind": "inspection", "discovery_id": "discovery-1", "payload": capture}]
    return artifacts


def test_evidenced_gap_has_partial_research_credit_and_no_data_coverage(artifacts):
    result = gap_artifacts(artifacts).evaluate()
    assert result["evaluator_version"] == 2
    assert result["status"] == "ok"
    assert result["source_precision"] == result["requirement_recall"] == 0
    assert result["research_recall"] == result["gap_credit"] == 0.5
    assert result["research_precision"] == result["gap_recall"] == 1
    assert result["quality"] == pytest.approx(2 / 3)
    assert result["coverage"] == [{"id": "history", "covered": False, "source_ids": []}]
    assert result["gap_coverage"] == [{"id": "history", "gap_reported": True, "claim_indexes": [0]}]


@pytest.mark.parametrize("kind", ["receipt", "trace", "both"])
def test_scoped_failed_probe_captures_prove_access_gap_without_crediting_duplicates(
    artifacts, kind
):
    gap_artifacts(artifacts)
    capture = artifacts.receipts[0]["payload"]
    result = {"probe_id": "denied-probe", "status": "failed", "captures": [capture]}
    artifacts.receipts = [{"kind": "probe", "discovery_id": "discovery-1", "payload": result}]
    if kind in {"trace", "both"}:
        artifacts.events = [
            {
                "event": "service_receipt",
                "kind": "probe",
                "discovery_id": "discovery-1",
                "result": result,
            }
        ]
    if kind == "trace":
        artifacts.receipts = []
    assert artifacts.evaluate()["quality"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("missing", ["capture_id", "body_sha256", "status_code", "url"])
def test_incomplete_capture_cannot_prove_a_gap(artifacts, missing):
    gap_artifacts(artifacts)
    artifacts.receipts[0]["payload"].pop(missing)
    assert artifacts.evaluate()["quality"] == 0


@pytest.mark.parametrize("status_code", [200, 403, 500, None, "401", True])
def test_wrong_or_malformed_response_status_does_not_match_independent_gap(artifacts, status_code):
    gap_artifacts(artifacts)
    artifacts.receipts[0]["payload"]["status_code"] = status_code
    assert artifacts.evaluate()["quality"] == 0


@pytest.mark.parametrize("scope", [None, "foreign"])
def test_gap_credit_requires_matching_discovery_scope(artifacts, scope):
    gap_artifacts(artifacts)
    artifacts.receipts[0]["discovery_id"] = scope
    assert artifacts.evaluate()["status"] == "invalid"
    artifacts.proposal.pop("registry")
    assert artifacts.evaluate()["quality"] == 0


def test_search_citation_and_agent_status_cannot_prove_a_blocker(artifacts):
    gap_artifacts(artifacts)
    artifacts.receipts[0].update(
        kind="search", payload={"results": [{"url": artifacts.source["url"]}]}
    )
    result = artifacts.evaluate()
    assert result["status"] == "ok"
    assert result["quality"] == result["gap_recall"] == 0


def test_outer_scope_cannot_override_a_conflicting_result_scope(artifacts):
    gap_artifacts(artifacts)
    artifacts.receipts[0]["payload"]["discovery_id"] = "foreign"
    result = artifacts.evaluate()
    assert result["status"] == "invalid"
    assert result["quality"] == 0


@pytest.mark.parametrize("change", ["status", "url", "unapproved"])
def test_evidence_does_not_credit_a_wrong_or_unapproved_gap_claim(artifacts, change):
    gap_artifacts(artifacts)
    if change == "status":
        artifacts.candidate["status"] = "needs_connector"
    elif change == "url":
        artifacts.specification["requirements"][0]["gap_any_of"][0]["url"] += "?different"
    else:
        artifacts.specification["requirements"][0]["gap_any_of"] = []
        artifacts.specification["requirements"][0]["any_of"] = [
            {"url": artifacts.source["url"], "connector": "json"}
        ]
    assert artifacts.evaluate()["quality"] == 0


def test_successful_http_observation_contradicts_access_gap(artifacts):
    gap_artifacts(artifacts)
    later = copy.deepcopy(artifacts.receipts[0])
    later["payload"].update(capture_id="capture-2", status_code=200)
    artifacts.receipts.append(later)
    assert artifacts.evaluate()["quality"] == 0


@pytest.mark.parametrize("missing", ["capture_id", "body_sha256"])
def test_incomplete_additional_observation_cannot_be_silently_dropped(artifacts, missing):
    gap_artifacts(artifacts)
    incomplete = copy.deepcopy(artifacts.receipts[0])
    incomplete["payload"]["capture_id"] = "incomplete"
    incomplete["payload"].pop(missing)
    artifacts.receipts.append(incomplete)
    assert artifacts.evaluate()["quality"] == 0


@pytest.mark.parametrize("status_code", [404, 410])
def test_captured_unavailability_can_receive_only_gap_credit(artifacts, status_code):
    result = gap_artifacts(artifacts, status="unavailable", status_code=status_code).evaluate()
    assert result["quality"] == pytest.approx(2 / 3)
    assert result["requirement_recall"] == 0


def test_conflicting_exports_of_one_capture_are_invalid(artifacts):
    gap_artifacts(artifacts)
    other = copy.deepcopy(artifacts.receipts[0])
    other["payload"]["body_sha256"] = "b" * 64
    artifacts.receipts.append(other)
    result = artifacts.evaluate()
    assert result["status"] == "invalid"
    assert result["quality"] == 0
    assert any("Conflicting service exports" in error for error in result["errors"])


@pytest.mark.parametrize(
    "content_type",
    [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ],
)
def test_unsupported_format_requires_matching_captured_content_type(artifacts, content_type):
    gap_artifacts(artifacts, status="needs_connector", status_code=200, content_type=content_type)
    artifacts.receipts[0]["payload"]["content_type"] = content_type.upper() + "; charset=binary"
    assert artifacts.evaluate()["quality"] == pytest.approx(2 / 3)
    artifacts.receipts[0]["payload"]["content_type"] = "application/json"
    assert artifacts.evaluate()["quality"] == 0
    artifacts.receipts[0]["payload"].pop("content_type")
    assert artifacts.evaluate()["quality"] == 0


def test_duplicate_gap_claims_reduce_precision_without_increasing_recall(artifacts):
    gap_artifacts(artifacts)
    artifacts.proposal["proposal"]["candidates"].append(copy.deepcopy(artifacts.candidate))
    result = artifacts.evaluate()
    assert result["research_precision"] == result["research_recall"] == 0.5
    assert result["gap_recall"] == 1
    assert result["quality"] == 0.5


def test_fulfilled_requirement_cannot_also_earn_gap_credit(artifacts):
    ready = copy.deepcopy(artifacts.candidate)
    probes, pipeline = artifacts.probes, artifacts.pipeline
    gap_artifacts(artifacts)
    artifacts.proposal["proposal"]["candidates"].append(ready)
    artifacts.probes, artifacts.pipeline = probes, pipeline
    artifacts.specification["requirements"][0]["any_of"] = [
        {"url": artifacts.source["url"], "connector": "json"}
    ]
    result = artifacts.evaluate()
    assert result["requirement_recall"] == result["research_recall"] == 1
    assert result["gap_recall"] == 0
    assert result["research_precision"] == 0.5


@pytest.mark.parametrize(
    "update",
    [
        {"status_code": True},
        {"status": "ready"},
        {"status_code": 200},
        {"status": "unavailable", "status_code": 500},
        {"status": "needs_connector", "status_code": 200, "content_type": "application/json"},
        {"content_type": "application/pdf"},
        {"typo": True},
    ],
)
def test_malformed_independent_gap_rules_are_rejected(artifacts, update):
    gap_artifacts(artifacts)
    artifacts.specification["requirements"][0]["gap_any_of"][0].update(update)
    with pytest.raises(ValueError):
        artifacts.evaluate()


def test_frontier_refuses_to_mix_new_and_legacy_scoring(artifacts):
    result = artifacts.evaluate()
    with pytest.raises(ValueError, match="same evaluator version"):
        frontier([result, {**result, "evaluator_version": 1}])
    legacy = {key: value for key, value in result.items() if key != "evaluator_version"}
    with pytest.raises(ValueError, match="same evaluator version"):
        frontier([result, legacy])


def test_changed_configuration_invalidates_even_apparently_complete_report(artifacts):
    artifacts.source["items_pointer"] = "/unprobed"
    artifacts.pipeline["sources"][0]["items_pointer"] = "/unprobed"
    result = artifacts.evaluate()
    assert result["status"] == "invalid"
    assert result["quality"] == 0
    assert any("source changed" in error for error in result["errors"])
    assert frontier([result]) == []


@pytest.mark.parametrize("record_count", [0, -1, True, "4", None])
def test_success_status_without_valid_record_count_is_not_a_probe(artifacts, record_count):
    artifacts.probes[0]["record_count"] = record_count
    assert artifacts.evaluate()["status"] == "invalid"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong-id", "failed", "other-run"])
def test_unusable_or_cross_discovery_probe_cannot_establish_readiness(artifacts, mutation):
    if mutation == "missing":
        artifacts.probes = []
    elif mutation == "duplicate":
        artifacts.probes.append(copy.deepcopy(artifacts.probes[0]))
    elif mutation == "wrong-id":
        artifacts.probes[0]["probe_id"] = "different"
    elif mutation == "failed":
        artifacts.probes[0]["status"] = "failed"
    else:
        artifacts.proposal["registry"] = {"discovery_id": "own-run"}
        artifacts.probes[0]["discovery_id"] = "other-run"
    assert artifacts.evaluate()["status"] == "invalid"


@pytest.mark.parametrize("mutation", ["missing", "changed", "invalid", "duplicate"])
def test_compiled_pipeline_must_be_valid_and_match_ready_sources(artifacts, mutation):
    if mutation == "missing":
        artifacts.pipeline = None
    elif mutation == "changed":
        artifacts.pipeline["sources"][0]["items_pointer"] = "/unprobed"
    elif mutation == "invalid":
        artifacts.pipeline["http"]["max_attempts"] = 0
    else:
        artifacts.pipeline["sources"].append(copy.deepcopy(artifacts.source))
    result = artifacts.evaluate()
    assert result["status"] == "invalid"
    assert result["quality"] == 0


def test_source_spec_is_validated_and_default_serialization_is_semantically_equal(artifacts):
    artifacts.source.pop("poll_interval_seconds")
    assert artifacts.evaluate()["status"] == "ok"
    artifacts.source["id_pointer"] = "invalid-pointer"
    assert artifacts.evaluate()["status"] == "invalid"


def test_actual_probe_report_and_domain_fingerprint_are_compatible(artifacts, store):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"items": [{"id": "notice-1"}]})
        )
    ) as client:
        report = probe_source(
            SourceSpec.model_validate(artifacts.source), store, client=client, public_only=False
        )
    assert report["status"] == "verified_sample"
    artifacts.probes = [report]
    artifacts.candidate["probe_id"] = report["probe_id"]
    assert artifacts.evaluate()["status"] == "ok"


def test_every_citation_must_be_observed_not_claimed_by_proposal_or_assistant(artifacts):
    invented = "https://invented.example/evidence"
    artifacts.candidate["evidence_urls"].append(invented)
    artifacts.proposal["observed_urls"] = [invented]
    artifacts.events = [
        {
            "event": "model_response",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "I verified this source.",
                            "annotations": [{"type": "url_citation", "url": invented}],
                        }
                    ],
                }
            ],
        }
    ]
    assert artifacts.evaluate()["status"] == "invalid"


@pytest.mark.parametrize("kind", ["native-search", "inspection", "search-receipt", "search-event"])
def test_actual_observation_shapes_establish_citation_evidence(artifacts, kind):
    evidence = "https://agency.example/documentation"
    artifacts.candidate["evidence_urls"] = [evidence]
    if kind == "native-search":
        artifacts.events = [
            {
                "event": "model_response",
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"sources": [{"url": evidence}]},
                    }
                ],
            }
        ]
    elif kind == "inspection":
        artifacts.events = [
            {
                "event": "tool_result",
                "tool": "inspect_url",
                "result": {"url": evidence, "capture_id": "capture-1"},
            }
        ]
    else:
        artifacts.proposal["registry"] = {"discovery_id": "discovery-1"}
        result = {
            "provider": "fixture",
            "query": "policy",
            "results": [{"url": evidence, "title": "Documentation"}],
        }
        if kind == "search-receipt":
            artifacts.receipts = [
                {
                    "id": "receipt-1",
                    "discovery_id": "discovery-1",
                    "operation_id": "search-1",
                    "kind": "search",
                    "payload": result,
                    "created_at": "2026-09-08T00:00:00Z",
                }
            ]
        else:
            artifacts.events = [
                {
                    "event": "service_receipt",
                    "receipt_id": "receipt-1",
                    "discovery_id": "discovery-1",
                    "kind": "search",
                    "result": result,
                }
            ]
    assert artifacts.evaluate()["status"] == "ok"


def test_search_receipt_cannot_claim_urls_absent_from_provider_results(artifacts):
    evidence = "https://unobserved.example/"
    artifacts.candidate["evidence_urls"] = [evidence]
    artifacts.receipts = [
        {"kind": "search", "payload": {"results": [], "observed_urls": [evidence]}}
    ]
    assert artifacts.evaluate()["status"] == "invalid"


def test_cross_discovery_receipt_is_rejected(artifacts):
    artifacts.proposal["registry"] = {"discovery_id": "own-run"}
    artifacts.receipts = [
        {
            "kind": "search",
            "discovery_id": "other-run",
            "payload": {"results": [{"url": artifacts.source["url"]}]},
        }
    ]
    assert artifacts.evaluate()["status"] == "invalid"


def test_failed_request_url_and_unknown_tool_result_are_not_observation(artifacts):
    evidence = "https://unobserved.example/"
    artifacts.candidate["evidence_urls"] = [evidence]
    artifacts.events = [
        {
            "event": "tool_result",
            "tool": "inspect_url",
            "result": {"url": evidence, "status": "error"},
        },
        {
            "event": "tool_result",
            "tool": "agent_assertion",
            "result": {"url": evidence, "capture_id": "made-up"},
        },
        {
            "event": "model_response",
            "output": [
                {
                    "type": "web_search_call",
                    "status": "failed",
                    "action": {"sources": [{"url": evidence}]},
                }
            ],
        },
    ]
    assert artifacts.evaluate()["status"] == "invalid"


def test_unsupported_source_keeps_honest_gap_and_requires_evidence(artifacts):
    artifacts.candidate.update(status="needs_connector", source=None, probe_id=None)
    artifacts.pipeline = None
    result = artifacts.evaluate()
    assert result["status"] == "ok"
    assert result["quality"] == 0
    artifacts.candidate["evidence_urls"] = ["https://invented.example/"]
    assert artifacts.evaluate()["status"] == "invalid"


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 20},
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": -1, "output_tokens": 5},
    ],
)
def test_missing_or_invalid_cost_remains_unknown_not_free(artifacts, usage):
    artifacts.proposal["usage"] = usage
    result = artifacts.evaluate()
    assert result["total_tokens"] is None
    assert frontier([result]) == []


def test_hashes_cover_exact_scored_bytes_and_optional_receipts(artifacts):
    artifacts.receipts = []
    first = artifacts.evaluate()
    assert set(first["artifact_sha256"]) == {
        "proposal.json",
        "probes.json",
        "pipeline.json",
        "trace.jsonl",
        "receipts.json",
    }
    for name, hashed in first["artifact_sha256"].items():
        assert hashed == digest((artifacts.path / name).read_bytes())
    artifacts.proposal["usage"]["input_tokens"] += 1
    second = artifacts.evaluate()
    assert first["artifact_sha256"]["proposal.json"] != second["artifact_sha256"]["proposal.json"]
    assert first["specification_sha256"] == second["specification_sha256"]


def test_frontier_retains_ties_and_tradeoffs_and_excludes_invalid_unknown_or_dominated():
    def point(quality, tokens, status="ok"):
        return {"quality": quality, "total_tokens": tokens, "status": status}

    cheap = point(0.5, 100)
    good = point(0.9, 200)
    tie = point(0.9, 200)
    assert frontier(
        [
            cheap,
            good,
            tie,
            point(0.8, 200),
            point(1, 0, "invalid"),
            point(1, None),
            point(float("nan"), 0),
        ]
    ) == [cheap, good, tie]
    with pytest.raises(ValueError, match="same specification"):
        frontier(
            [{**cheap, "specification_sha256": "one"}, {**good, "specification_sha256": "two"}]
        )


@pytest.mark.parametrize(
    "requirements",
    [
        [],
        [{"id": "x", "any_of": []}],
        [
            {
                "id": "x",
                "weight": float("nan"),
                "any_of": [{"url": "https://example.com", "connector": "json"}],
            }
        ],
        [
            {
                "id": "x",
                "any_of": [{"url": "https://example.com", "connector": "json", "typo": True}],
            }
        ],
    ],
)
def test_empty_or_invalid_independent_requirements_are_rejected(requirements):
    with pytest.raises(ValueError):
        validate_requirements(requirements)


def test_module_entrypoint_scores_artifacts_without_altering_inputs(artifacts, tmp_path):
    artifacts.write()
    specification = tmp_path / "requirements.json"
    specification.write_text(json.dumps(artifacts.specification), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in artifacts.path.iterdir()}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_harness.evaluation",
            str(specification),
            str(artifacts.path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(completed.stdout)
    assert report["results"][0]["quality"] == pytest.approx(2 / 3)
    assert report["frontier"] == report["results"]
    assert before == {path.name: path.read_bytes() for path in artifacts.path.iterdir()}


def test_module_entrypoint_rejects_completion_claim_without_saved_proposal(artifacts, tmp_path):
    artifacts.write()
    (artifacts.path / "proposal.json").unlink()
    specification = tmp_path / "requirements.json"
    specification.write_text(json.dumps(artifacts.specification), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_harness.evaluation",
            str(specification),
            str(artifacts.path),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "Evaluation failed" in completed.stderr
    assert completed.stdout == ""
