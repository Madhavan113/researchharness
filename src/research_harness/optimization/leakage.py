"""Advisory development-string audit; neither an evaluator nor an overfitting proof."""

from __future__ import annotations

import re
from collections import defaultdict

from research_harness.evaluation.benchmark import LoadedBenchmark, fixture_urls, source_host
from research_harness.util import canonical_json, digest

POLICY = "development-strings-v1"
PHRASE_WORDS = 5
MAX_FINDINGS = 200


def _phrases(text: str) -> set[str]:
    words = re.findall(r"\w+", text.casefold())
    return {" ".join(words[i : i + PHRASE_WORDS]) for i in range(len(words) - PHRASE_WORDS + 1)}


def audit_inputs(benchmark: LoadedBenchmark) -> dict:
    """Freeze only public task fields and source-fixture URLs, never expected answers."""
    if benchmark.manifest.split != "development":
        raise ValueError("Leakage audit accepts development inputs only")
    terms = defaultdict(set)
    for case in benchmark.cases.values():
        task = case.task()
        terms[("case_id", task["id"])].add(task["id"])
        for phrase in _phrases(task["brief"]):
            terms[("brief_phrase", phrase)].add(task["id"])
        for url in fixture_urls(benchmark, case):
            terms[("url", url)].add(task["id"])
            terms[("host", source_host(url))].add(task["id"])
    return {
        "schema_version": 1,
        "policy": POLICY,
        "benchmark_sha256": benchmark.sha256,
        "fixture_sha256": benchmark.fixture_sha256,
        "terms": [
            {"kind": kind, "term": term, "case_ids": sorted(cases)}
            for (kind, term), cases in sorted(terms.items())
        ],
    }


def audit_candidate(inputs: dict, *, source: bytes, instructions: bytes) -> dict:
    """Scan text without executing it; findings are advisory and deterministically bounded."""
    if inputs["schema_version"] != 1 or inputs["policy"] != POLICY:
        raise ValueError("Unsupported leakage audit policy")
    files = {"strategy.py": source, "instructions.md": instructions}
    findings, total = [], 0
    for name, raw in files.items():
        text = raw.decode("utf-8", errors="replace")
        phrases = _phrases(text)
        for entry in inputs["terms"]:
            kind, term = entry["kind"], entry["term"]
            if kind == "brief_phrase":
                matched = term in phrases
            else:
                boundary = r"\w.-" if kind == "host" else r"\w-"
                end = r"\w/?#&=%+.-" if kind == "url" else boundary
                pattern = rf"(?<![{boundary}]){re.escape(term)}(?![{end}])"
                matched = re.search(pattern, text, re.IGNORECASE) is not None
            if matched:
                total += 1
                if len(findings) < MAX_FINDINGS:
                    findings.append({"file": name, **entry})
    return {
        "schema_version": 1,
        "policy": POLICY,
        "inputs_sha256": digest(canonical_json(inputs)),
        "file_sha256": {name: digest(raw) for name, raw in files.items()},
        "status": "leak_suspect" if total else "no_match",
        "findings": findings,
        "total_findings": total,
        "truncated": total > len(findings),
        "limitations": [
            "Advisory literal/word-sequence matches; generic overlap may be benign.",
            "No-match is not clearance: obfuscation, short phrases and semantic copying may escape.",
            "Manual review remains separate; no held-out inputs or evaluator predicates are scanned.",
        ],
    }
