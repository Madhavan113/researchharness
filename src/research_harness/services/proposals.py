"""Compile proposals against service-owned evidence and render source assessments."""

from __future__ import annotations

from typing import Any

from research_harness.config import PipelineSpec
from research_harness.discovery_models import ProposalDraft


def compile_proposal(
    draft: ProposalDraft,
    probes: dict[str, dict[str, Any]],
    observed_urls: set[str],
) -> tuple[PipelineSpec | None, list[str]]:
    ids = [need.id for need in draft.needs]
    if len(ids) != len(set(ids)):
        raise ValueError("Data need ids must be unique")
    sources = []
    covered: set[str] = set()
    for candidate in draft.candidates:
        unknown = set(candidate.covers) - set(ids)
        if unknown:
            raise ValueError(f"{candidate.name}: unknown data needs {sorted(unknown)}")
        if not candidate.covers:
            raise ValueError(f"{candidate.name}: identify the data need this source serves")
        if not candidate.evidence_urls or not all(
            url in observed_urls for url in candidate.evidence_urls
        ):
            raise ValueError(
                f"{candidate.name}: every cited URL must be actually observed during discovery"
            )
        if candidate.status != "ready":
            continue
        if not candidate.source or not candidate.source.enabled:
            raise ValueError(f"{candidate.name}: ready sources require an enabled SourceSpec")
        proof = probes.get(candidate.probe_id or "")
        if not proof or proof["status"] != "verified_sample":
            raise ValueError(f"{candidate.name}: a successful probe is required")
        if proof["source_fingerprint"] != candidate.source.fingerprint():
            raise ValueError(
                f"{candidate.name}: configuration changed after probing; probe the exact source again"
            )
        sources.append(candidate.source)
        covered.update(candidate.covers)
    gaps = [need.id for need in draft.needs if need.required and need.id not in covered]
    pipeline = (
        PipelineSpec(name=draft.name, description=draft.research_question, sources=sources)
        if sources
        else None
    )
    return pipeline, gaps


def render_proposal(draft: ProposalDraft, gaps: list[str]) -> str:
    lines = [f"# {draft.title}", "", draft.research_question, "", "## Data requirements", ""]
    for need in draft.needs:
        lines.append(
            f"- **{need.id}** ({'required' if need.required else 'optional'}): {need.description}"
        )
    lines.extend(["", "## Source assessment", ""])
    for candidate in draft.candidates:
        lines.extend(
            [
                f"### {candidate.name}",
                "",
                f"Status: **{candidate.status}**. Covers: {', '.join(candidate.covers)}.",
                "",
                candidate.purpose,
                "",
                f"Freshness: {candidate.freshness_assessment}",
                "",
                f"History: {candidate.historical_coverage}",
                "",
                f"Access: {candidate.access_notes}",
                "",
            ]
        )
        for index, url in enumerate(candidate.evidence_urls, 1):
            lines.append(f"- [Source evidence {index}]({url})")
        if candidate.probe_id:
            lines.extend(
                ["", f"Probe: `{candidate.probe_id}`. This is a sampled compatibility check."]
            )
        if candidate.limitations:
            lines.append("")
            lines.extend(f"- {limitation}" for limitation in candidate.limitations)
        lines.append("")
    lines.extend(
        [
            "## Coverage gaps",
            "",
            ", ".join(gaps)
            if gaps
            else "Every required need has a source that passed a sample probe. Semantic completeness still needs review.",
            "",
        ]
    )
    if draft.open_questions:
        lines.extend(["## Open questions", ""])
        lines.extend(f"- {question}" for question in draft.open_questions)
        lines.append("")
    lines.extend(
        [
            "## Execution",
            "",
            "`pipeline.json`, when present, contains only sources backed by matching successful probes. Run it with `rh run pipeline.json`. Polling intervals are configuration; no background schedule is installed. The full ingestion run validates pagination and publishes each source atomically.",
            "",
        ]
    )
    return "\n".join(lines)
