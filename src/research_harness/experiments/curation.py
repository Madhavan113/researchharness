"""Operator curation of a benchmark package, separate from candidate files.

The local OS account is the trust boundary, not a claimed reviewer name. Keep
this database and the evidence outside worker mounts. This API cannot establish
human presence and does not authorize a model run or accept a research finding.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from research_harness.experiments.checks import collect_results, status
from research_harness.experiments.package import files, load_prepared
from research_harness.util import canonical_json, digest, timestamp


def evidence(prepared: Path, check: Path) -> dict:
    """Recheck raw control results, not just the runner's success summary."""
    prepared, check = prepared.resolve(), check.resolve()
    try:
        package = load_prepared(prepared)
        report = status(check)
        if report["status"] not in {"passed", "failed", "error", "timed_out", "interrupted"}:
            raise ValueError("Review requires a finished benchmark check")
        if (
            report["kind"] != "benchmark_control_check"
            or report["input_sha256"] != package["input_sha256"]
            or Path(report["prepared"]).resolve() != prepared
            or report["harbor_version"] != package["harbor_version"]
        ):
            raise ValueError("Benchmark check does not belong to this prepared package")
        controls = collect_results(prepared, check, package)
        passed = (
            report["status"] == "passed"
            and report.get("exit_code") == 0
            and controls["controls_passed"]
            and all(report.get(key) == value for key, value in controls.items())
        )
        subject = {
            "experiment_id": package["experiment"]["id"],
            "input_sha256": package["input_sha256"],
            "check_sha256": digest(canonical_json(files(check))),
        }
        return {
            **subject,
            "subject_sha256": digest(canonical_json(subject)),
            "prepared": str(prepared),
            "check": str(check),
            "question": package["experiment"]["question"],
            "plan": (prepared / "inputs/plan.md").read_text(),
            "tasks": package["tasks"],
            "check_status": report["status"],
            "controls_passed": passed,
            "controls": controls,
        }
    except (KeyError, TypeError) as exc:
        raise ValueError("Malformed prepared package or benchmark check") from exc


class CuratorStore:
    """Append decisions; serialize writers and require the head the operator saw.

    Each experiment has one current decision. Reviewing another version replaces
    the current decision without erasing it. An old acceptance cannot become
    current again by selecting an older package or check directory.
    """

    def __init__(self, path: Path):
        self.path = path.resolve()

    def _separate(self, prepared: Path, check: Path) -> None:
        if any(self.path.is_relative_to(path.resolve()) for path in (prepared, check)):
            raise ValueError("Curator database must be outside the prepared package and check")

    @contextmanager
    def _connect(self, *, write: bool = False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        mode = "rw" if write else "ro"
        db = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS reviews ("
                    "sequence INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, "
                    "experiment_id TEXT NOT NULL, record TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS reviews_experiment "
                    "ON reviews(experiment_id, sequence)"
                )
                db.commit()
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        finally:
            db.close()

    @staticmethod
    def _history(db: sqlite3.Connection, experiment_id: str) -> list[dict]:
        # A reader may arrive between file creation and schema commit, including
        # after an interrupted first write. An empty store grants no acceptance.
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reviews'"
        ).fetchone():
            return []
        return [
            json.loads(row["record"])
            for row in db.execute(
                "SELECT record FROM reviews WHERE experiment_id = ? ORDER BY sequence",
                (experiment_id,),
            )
        ]

    def history(self, experiment_id: str) -> list[dict]:
        if not self.path.exists():
            return []
        with self._connect() as db:
            return self._history(db, experiment_id)

    def inspect(self, prepared: Path, check: Path) -> dict:
        self._separate(prepared, check)
        snapshot = evidence(prepared, check)
        history = self.history(snapshot["experiment_id"])
        current = history[-1] if history else None
        state = "pending_review"
        if current:
            if current["decision"] == "withdraw":
                state = "withdrawn"
            elif current["subject_sha256"] != snapshot["subject_sha256"]:
                state = "pending_review"
            else:
                state = "accepted" if current["decision"] == "accept" else "rejected"
        # Even a stored acceptance cannot override current evidence validation.
        if state == "accepted" and not snapshot["controls_passed"]:
            state = "invalid_evidence"
        return {
            **snapshot,
            "curation_status": state,
            "head": current["id"] if current else "none",
            "current_review": current,
            "execution_authorized": False,
            "finding_accepted": False,
        }

    def require_accepted(self, prepared: Path, check: Path) -> dict:
        """A prerequisite for a future dispatcher, not execution authorization."""
        review = self.inspect(prepared, check)
        if review["curation_status"] != "accepted":
            raise ValueError(f"Benchmark is not accepted: {review['curation_status']}")
        return review

    def decide(
        self,
        prepared: Path,
        check: Path,
        *,
        decision: Literal["accept", "reject"],
        subject: str,
        after: str,
        reason: str,
    ) -> dict:
        if decision not in {"accept", "reject"}:
            raise ValueError("Decision must be accept or reject")
        self._separate(prepared, check)
        snapshot = evidence(prepared, check)
        if subject != snapshot["subject_sha256"]:
            raise ValueError("Review subject changed; inspect the current package and check")
        if decision == "accept" and not snapshot["controls_passed"]:
            raise ValueError("Acceptance requires verified positive and negative controls")
        return self._append(
            snapshot["experiment_id"],
            decision=decision,
            after=after,
            reason=reason,
            subject={
                key: snapshot[key]
                for key in (
                    "subject_sha256",
                    "input_sha256",
                    "check_sha256",
                    "prepared",
                    "check",
                )
            },
        )

    def withdraw(self, experiment_id: str, *, after: str, reason: str) -> dict:
        # Withdrawal must still work when the original files are gone or damaged.
        return self._append(experiment_id, decision="withdraw", after=after, reason=reason)

    def _append(
        self, experiment_id: str, *, decision: str, after: str, reason: str, subject=None
    ) -> dict:
        if not reason.strip():
            raise ValueError("Record a reason for the curator decision")
        if not after:
            raise ValueError("Supply the review head you inspected (or 'none')")
        with self._connect(write=True) as db:
            history = self._history(db, experiment_id)
            current = history[-1] if history else None
            head = current["id"] if current else "none"
            if after != head:
                raise ValueError("Review history changed; inspect the latest decision first")
            if decision == "withdraw":
                if not current or current["decision"] != "accept":
                    raise ValueError("There is no current acceptance to withdraw")
                subject = {
                    key: current[key]
                    for key in (
                        "subject_sha256",
                        "input_sha256",
                        "check_sha256",
                        "prepared",
                        "check",
                    )
                }
            record = {
                "schema_version": 1,
                "id": str(uuid.uuid4()),
                "experiment_id": experiment_id,
                "scope": "benchmark_package",
                "created_at": timestamp(),
                "decision": decision,
                "reason": reason.strip(),
                "supersedes": current["id"] if current else None,
                "actor": {
                    "kind": "local_os_account",
                    "uid": os.getuid() if hasattr(os, "getuid") else None,
                    "human_presence_verified": False,
                },
                **subject,
            }
            db.execute(
                "INSERT INTO reviews(id, experiment_id, record) VALUES (?, ?, ?)",
                (record["id"], experiment_id, canonical_json(record)),
            )
        return record
