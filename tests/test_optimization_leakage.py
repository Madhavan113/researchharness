from dataclasses import replace
from pathlib import Path

import pytest

from research_harness.evaluation.benchmark import load_benchmark
from research_harness.optimization.leakage import MAX_FINDINGS, audit_candidate, audit_inputs
from research_harness.util import canonical_json, digest

DEVELOPMENT = Path(__file__).resolve().parents[1] / "examples/evaluation/development/manifest.json"


def test_audit_uses_public_tasks_and_fixture_urls_without_private_evaluator_fields():
    benchmark = load_benchmark(DEVELOPMENT)
    case = benchmark.cases["export-notices"]
    case.specification["requirements"][0]["any_of"][0]["url"] = "https://secret.invalid/answer"
    case.sources[0].url = "https://secret.invalid/source"
    case.review_notes = "EVALUATOR-CANARY"
    case.manual_checks = ["EVALUATOR-CANARY"]
    inputs = audit_inputs(replace(benchmark, cases={case.id: case}))
    assert "secret.invalid" not in canonical_json(inputs)
    assert "EVALUATOR-CANARY" not in canonical_json(inputs)
    report = audit_candidate(
        inputs,
        source=b'# https://export-office.fixture.example/notices.json\ncase = "export-notices"',
        instructions=b"Prefer EXPORT-OFFICE.FIXTURE.EXAMPLE for research.",
    )
    assert report["status"] == "leak_suspect"
    assert {f["kind"] for f in report["findings"]} == {"url", "host", "case_id"}
    assert {f["file"] for f in report["findings"]} == {"strategy.py", "instructions.md"}
    assert report["inputs_sha256"] == digest(canonical_json(inputs))
    assert all(f["case_ids"] == [case.id] for f in report["findings"])
    assert not report["truncated"]


def test_audit_word_phrases_normalize_whitespace_punctuation_and_case():
    benchmark = load_benchmark(DEVELOPMENT)
    case = benchmark.cases["export-notices"]
    case.brief = "Find official notices with stable publication timestamps."
    report = audit_candidate(
        audit_inputs(replace(benchmark, cases={case.id: case})),
        source=b"# FIND, official\n# notices with STABLE publication timestamps.",
        instructions=b"Task agnostic research instructions.",
    )
    assert report["status"] == "leak_suspect"
    assert report["total_findings"] == 3
    assert {f["kind"] for f in report["findings"]} == {"brief_phrase"}


def test_no_match_does_not_treat_identifier_or_hostname_prefixes_as_task_strings():
    inputs = audit_inputs(load_benchmark(DEVELOPMENT))
    report = audit_candidate(
        inputs,
        source=b'# export-notices-v2\nurl = "https://export-office.fixture.example.evil/path"',
        instructions=b"Use stored evidence to guide each next research step.",
    )
    assert report["status"] == "no_match"
    assert report["findings"] == [] and report["total_findings"] == 0
    assert any("not clearance" in limitation for limitation in report["limitations"])


def test_audit_refuses_heldout_before_reading_any_case_or_fixture():
    benchmark = load_benchmark(DEVELOPMENT)
    heldout = replace(
        benchmark, manifest=benchmark.manifest.model_copy(update={"split": "heldout"})
    )
    with pytest.raises(ValueError, match="development inputs only"):
        audit_inputs(replace(heldout, path=Path("/must-not-be-read/manifest.json")))


def test_findings_are_bounded_and_counted_without_silently_dropping_matches():
    inputs = audit_inputs(load_benchmark(DEVELOPMENT))
    terms = [f"task-{i}" for i in range(MAX_FINDINGS + 5)]
    inputs["terms"] = [{"kind": "case_id", "term": term, "case_ids": [term]} for term in terms]
    source = "\n".join(terms).encode()
    report = audit_candidate(inputs, source=source, instructions=b"General instructions")
    assert report["truncated"] and len(report["findings"]) == MAX_FINDINGS
    assert report["total_findings"] == len(terms)
    assert report == audit_candidate(inputs, source=source, instructions=b"General instructions")
