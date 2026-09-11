"""Registry of research questions and the alternative pipelines proposed for them.

The registry lives in the same store as collected data. Every candidate pipeline definition is
kept with its fingerprint, origin, and status history, so agents can compare alternatives,
adopt one, and later see which definition produced which runs."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from research_harness.config import PipelineSpec
from research_harness.store import Store
from research_harness.util import canonical_json, digest, timestamp

PIPELINE_STATUSES = ("proposed", "adopted", "superseded", "retired")
ORIGINS = ("discovery", "manual")
ID_PREFIX = re.compile(r"^[0-9a-f]{6,32}$")


def _now(store: Store) -> str:
    return timestamp(store.clock())


def default_title(brief: str) -> str:
    first = next((line.strip() for line in brief.splitlines() if line.strip()), "Untitled")
    first = first.lstrip("#").strip()
    return first if len(first) <= 120 else first[:117] + "..."


def register_question(store: Store, brief: str, *, title: str | None = None) -> dict[str, Any]:
    """Create or return the question for this exact brief text."""
    brief = brief.strip()
    if not brief:
        raise ValueError("The research brief is empty")
    brief_hash = digest(brief)
    row = {
        "id": uuid4().hex,
        "brief": brief,
        "brief_hash": brief_hash,
        "title": (title or default_title(brief)).strip()[:200],
        "created_at": _now(store),
    }
    with store.transaction():
        inserted = store.execute(
            "INSERT INTO questions (id, brief, brief_hash, title, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(brief_hash) DO NOTHING RETURNING id",
            (row["id"], row["brief"], row["brief_hash"], row["title"], row["created_at"]),
        ).fetchone()
        if inserted is None:
            existing = store.query_one("SELECT * FROM questions WHERE brief_hash=?", (brief_hash,))
            if existing is None:
                raise RuntimeError("Registered question disappeared after a conflicting insert")
            return {**existing, "created": False}
    return {**row, "created": True}


def get_question(store: Store, question_id: str) -> dict[str, Any]:
    row = store.query_one(
        "SELECT * FROM questions WHERE id=?", (resolve_id(store, "questions", question_id),)
    )
    if not row:
        raise ValueError(f"Unknown question: {question_id}")
    return row


def list_questions(store: Store) -> list[dict[str, Any]]:
    rows = store.query(
        """SELECT q.id, q.title, q.created_at,
                  COUNT(p.id) AS pipelines,
                  SUM(CASE WHEN p.status='adopted' THEN 1 ELSE 0 END) AS adopted,
                  (SELECT COUNT(*) FROM discoveries d WHERE d.question_id=q.id) AS discoveries
           FROM questions q LEFT JOIN pipeline_versions p ON p.question_id=q.id
           GROUP BY q.id, q.title, q.created_at ORDER BY q.created_at DESC, q.id"""
    )
    for row in rows:
        row["pipelines"] = int(row["pipelines"] or 0)
        row["adopted"] = int(row["adopted"] or 0)
        row["discoveries"] = int(row["discoveries"] or 0)
    return rows


def start_discovery(store: Store, question_id: str, *, model: str, output_ref: str | None) -> str:
    discovery_id = uuid4().hex
    with store.transaction():
        store.execute(
            "INSERT INTO discoveries (id, question_id, model, status, output_ref, started_at) VALUES (?, ?, ?, 'running', ?, ?)",
            (discovery_id, question_id, model, output_ref, _now(store)),
        )
    return discovery_id


def finish_discovery(
    store: Store,
    discovery_id: str,
    *,
    status: str,
    usage: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with store.transaction():
        store.execute(
            "UPDATE discoveries SET status=?, finished_at=?, usage_json=?, error=? WHERE id=?",
            (status, _now(store), canonical_json(usage or {}), error, discovery_id),
        )


def record_proposal(
    store: Store,
    *,
    discovery_id: str,
    question_id: str,
    result: dict[str, Any],
    proposal_md: str,
) -> str:
    proposal_id = uuid4().hex
    with store.transaction():
        store.execute(
            "INSERT INTO proposals (id, discovery_id, question_id, status, proposal_json, proposal_md, gaps_json, verified_source_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                discovery_id,
                question_id,
                result["status"],
                canonical_json(result),
                proposal_md,
                canonical_json(result.get("uncovered_required_needs", [])),
                int(result.get("verified_source_count", 0)),
                _now(store),
            ),
        )
    return proposal_id


def _event(
    store: Store,
    pipeline_id: str,
    event: str,
    *,
    from_status: str | None,
    to_status: str | None,
    note: str | None,
) -> None:
    store.execute(
        "INSERT INTO pipeline_events (pipeline_version_id, at, event, from_status, to_status, note) VALUES (?, ?, ?, ?, ?, ?)",
        (pipeline_id, _now(store), event, from_status, to_status, note),
    )


def register_pipeline(
    store: Store,
    question_id: str,
    spec: PipelineSpec,
    *,
    proposal_id: str | None = None,
    origin: str = "manual",
    label: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Store a candidate definition. The same definition for the same question is returned as is."""
    if origin not in ORIGINS:
        raise ValueError(f"Unknown pipeline origin: {origin}")
    question = get_question(store, question_id)
    fingerprint = spec.fingerprint()
    now = _now(store)
    pipeline_id = uuid4().hex
    with store.transaction():
        inserted = store.execute(
            "INSERT INTO pipeline_versions (id, question_id, proposal_id, name, fingerprint, spec_json, status, origin, label, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?) ON CONFLICT(question_id, fingerprint) DO NOTHING RETURNING id",
            (
                pipeline_id,
                question["id"],
                proposal_id,
                spec.name,
                fingerprint,
                canonical_json(spec.model_dump(mode="json")),
                origin,
                label,
                notes,
                now,
                now,
            ),
        ).fetchone()
        if inserted is None:
            existing = store.query_one(
                "SELECT * FROM pipeline_versions WHERE question_id=? AND fingerprint=?",
                (question["id"], fingerprint),
            )
            if existing is None:
                raise RuntimeError("Registered pipeline disappeared after a conflicting insert")
            return {**_public(existing), "created": False}
        _event(store, pipeline_id, "registered", from_status=None, to_status="proposed", note=notes)
    return {**get_pipeline(store, pipeline_id), "created": True}


def resolve_id(store: Store, table: str, value: str) -> str:
    """Accept a full id or a unique hex prefix of at least six characters."""
    if table not in {"questions", "pipeline_versions", "discoveries", "proposals"}:
        raise ValueError(f"Unknown registry table: {table}")
    value = value.strip().lower()
    if not ID_PREFIX.match(value):
        raise ValueError(f"Not a registry id or id prefix: {value!r}")
    if len(value) == 32:
        return value
    matches = store.query(f"SELECT id FROM {table} WHERE id LIKE ? LIMIT 3", (value + "%",))
    if len(matches) == 1:
        return matches[0]["id"]
    if not matches:
        raise ValueError(f"No {table} entry starts with {value}")
    raise ValueError(f"Ambiguous prefix {value}; supply more characters")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in row.items() if key != "spec_json"}
    result["spec"] = json.loads(row["spec_json"])
    return result


def get_pipeline(store: Store, pipeline_id: str) -> dict[str, Any]:
    row = store.query_one(
        "SELECT * FROM pipeline_versions WHERE id=?",
        (resolve_id(store, "pipeline_versions", pipeline_id),),
    )
    if not row:
        raise ValueError(f"Unknown pipeline: {pipeline_id}")
    return _public(row)


def pipeline_spec(store: Store, pipeline_id: str) -> tuple[dict[str, Any], PipelineSpec]:
    row = get_pipeline(store, pipeline_id)
    return row, PipelineSpec.model_validate(row["spec"])


def list_pipelines(
    store: Store, *, question_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    if status is not None and status not in PIPELINE_STATUSES:
        raise ValueError(f"Unknown status {status}; use one of {', '.join(PIPELINE_STATUSES)}")
    clauses = []
    params: list[Any] = []
    if question_id is not None:
        clauses.append("p.question_id=?")
        params.append(resolve_id(store, "questions", question_id))
    if status is not None:
        clauses.append("p.status=?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = store.query(
        f"""SELECT p.id, p.question_id, q.title AS question_title, p.proposal_id, p.name,
                   p.fingerprint, p.status, p.origin, p.label, p.notes, p.created_at, p.updated_at
            FROM pipeline_versions p JOIN questions q ON q.id=p.question_id
            {where} ORDER BY p.created_at DESC, p.id""",
        tuple(params),
    )
    for row in rows:
        spec = store.query_one("SELECT spec_json FROM pipeline_versions WHERE id=?", (row["id"],))
        sources = json.loads(spec["spec_json"])["sources"] if spec else []
        row["sources"] = [source["id"] for source in sources]
        row["latest_run"] = store.latest_run(
            f"{row['question_id']}/{row['name']}", row["fingerprint"]
        )
    return rows


def set_status(
    store: Store, pipeline_id: str, status: str, *, note: str | None = None
) -> dict[str, Any]:
    if status not in {"proposed", "retired"}:
        raise ValueError(
            "Use adopt for adoption; statuses settable directly are proposed and retired"
        )
    row = get_pipeline(store, pipeline_id)
    with store.transaction():
        _transition(store, row, status, "retired" if status == "retired" else "reopened", note)
    return get_pipeline(store, row["id"])


def _transition(
    store: Store, row: dict[str, Any], status: str, event: str, note: str | None
) -> None:
    store.execute(
        "UPDATE pipeline_versions SET status=?, updated_at=? WHERE id=?",
        (status, _now(store), row["id"]),
    )
    _event(store, row["id"], event, from_status=row["status"], to_status=status, note=note)


def adopt_pipeline(store: Store, pipeline_id: str, *, note: str | None = None) -> dict[str, Any]:
    """Adopt one definition for its question; the previously adopted one becomes superseded."""
    row = get_pipeline(store, pipeline_id)
    if row["status"] == "retired":
        raise ValueError("A retired pipeline cannot be adopted; reopen it first")
    with store.transaction():
        for current in store.query(
            "SELECT * FROM pipeline_versions WHERE question_id=? AND status='adopted' AND id<>?",
            (row["question_id"], row["id"]),
        ):
            _transition(store, current, "superseded", "superseded", f"Superseded by {row['id']}")
        if row["status"] != "adopted":
            _transition(store, row, "adopted", "adopted", note)
    return get_pipeline(store, row["id"])


def retire_pipeline(store: Store, pipeline_id: str, *, note: str | None = None) -> dict[str, Any]:
    return set_status(store, pipeline_id, "retired", note=note)


def pipeline_history(store: Store, pipeline_id: str) -> list[dict[str, Any]]:
    resolved = resolve_id(store, "pipeline_versions", pipeline_id)
    return store.query(
        "SELECT at, event, from_status, to_status, note FROM pipeline_events WHERE pipeline_version_id=? ORDER BY id",
        (resolved,),
    )


def question_overview(store: Store, question_id: str) -> dict[str, Any]:
    question = get_question(store, question_id)
    discoveries = store.query(
        "SELECT id, model, status, output_ref, started_at, finished_at, error FROM discoveries WHERE question_id=? ORDER BY started_at DESC",
        (question["id"],),
    )
    proposals = store.query(
        "SELECT id, discovery_id, status, verified_source_count, gaps_json, created_at FROM proposals WHERE question_id=? ORDER BY created_at DESC",
        (question["id"],),
    )
    for proposal in proposals:
        proposal["uncovered_required_needs"] = json.loads(proposal.pop("gaps_json"))
    return {
        **question,
        "discoveries": discoveries,
        "proposals": proposals,
        "pipelines": list_pipelines(store, question_id=question["id"]),
    }
