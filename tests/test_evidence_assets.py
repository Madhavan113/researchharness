from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from research_harness.evaluation import evidence_assets as assets

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "examples/evaluation/evidence/new-checkpoint"
EMPTY_POLICY = {"schema_version": 1, "legacy_archives": {}, "legacy_checkpoint_bytes": {}}


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    (root / "response.bin").write_bytes(bytes(range(256)))
    (root / "empty.txt").write_bytes(b"")
    folder = root / "nested"
    folder.mkdir()
    (folder / "strategy.py").write_text("raise RuntimeError('must never execute')\n")
    (folder / "strategy.py").chmod(0o755)
    return root


@pytest.fixture
def options():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    return {
        "repository": "example/research",
        "source_commit": commit,
        "tag": "checkpoint-2026-09-12",
        "source_repository": ROOT,
    }


@pytest.fixture
def prepared(source, tmp_path, options):
    output = tmp_path / "assets"
    assets.prepare(source, output, **options)
    return output


def verify(output):
    return assets.verify(
        output / "index.json", output / "artifacts.tar.gz", output / "files.json.gz"
    )


def rebind(output, key):
    path = output / "index.json"
    index = json.loads(path.read_bytes())
    asset = output / index[key]["name"]
    raw = asset.read_bytes()
    index[key].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    path.write_text(json.dumps(index))


def replace_archive(output, members):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for info, data in members:
            archive.addfile(info, io.BytesIO(data) if info.isfile() else None)
    (output / "artifacts.tar.gz").write_bytes(gzip.compress(raw.getvalue(), mtime=0))
    rebind(output, "archive")


def members(output):
    with tarfile.open(output / "artifacts.tar.gz", "r:gz") as archive:
        return [(info, archive.extractfile(info).read()) for info in archive]


def test_preparation_is_deterministic_and_preserves_exact_bytes(
    source, prepared, tmp_path, options
):
    first = {path.name: path.read_bytes() for path in prepared.iterdir()}
    for path in source.rglob("*"):
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    second = tmp_path / "again"
    index = assets.prepare(source, second, **options)
    assert first == {path.name: path.read_bytes() for path in second.iterdir()}
    assert index["availability"] == "prepared"
    assert index["archive"]["url"].endswith("/checkpoint-2026-09-12/artifacts.tar.gz")
    assert verify(second) == {"verified": True, "file_count": 3, "bytes": 297}
    for member, raw in members(second):
        assert raw == (source / member.name).read_bytes()
        assert not (member.uid or member.gid or member.uname or member.gname or member.mtime)
    assert not (second / "index.pending.json").exists()


@pytest.mark.parametrize("name", ["artifacts.tar.gz", "files.json.gz"])
def test_changed_compressed_assets_fail_before_parsing(prepared, name):
    path = prepared / name
    raw = path.read_bytes()
    path.write_bytes(b"!" + raw[1:])
    with pytest.raises(ValueError, match="asset hash or size"):
        verify(prepared)


def test_member_hash_is_checked_even_if_archive_hash_is_rebound(prepared):
    records = members(prepared)
    record, raw = next(item for item in records if item[0].name == "response.bin")
    records = [(info, b"!" + data[1:] if info is record else data) for info, data in records]
    replace_archive(prepared, records)
    with pytest.raises(ValueError, match="member hash"):
        verify(prepared)
    assert raw == bytes(range(256))


@pytest.mark.parametrize(
    "change",
    [
        "traversal",
        "absolute",
        "backslash",
        "duplicate",
        "symlink",
        "hardlink",
        "directory",
        "extra",
        "missing",
        "mode",
        "hostname",
        "oversized",
    ],
)
def test_unsafe_or_incomplete_archives_fail_without_extraction(prepared, tmp_path, change):
    records = members(prepared)
    info, raw = records[0]
    if change in {"traversal", "absolute", "backslash", "extra"}:
        info.name = {
            "traversal": "../escape",
            "absolute": "/escape",
            "backslash": "..\\escape",
            "extra": "unexpected",
        }[change]
    elif change == "duplicate":
        records.append(records[0])
    elif change in {"symlink", "hardlink", "directory"}:
        info.type = {
            "symlink": tarfile.SYMTYPE,
            "hardlink": tarfile.LNKTYPE,
            "directory": tarfile.DIRTYPE,
        }[change]
        info.linkname = "../escape" if change != "directory" else ""
        info.size = 0
    elif change == "missing":
        records.pop()
    elif change == "mode":
        info.mode = 0o777
    elif change == "hostname":
        info.uname = "operator"
    else:
        info.size = assets.MAX_BYTES + 1
        (prepared / "artifacts.tar.gz").write_bytes(gzip.compress(info.tobuf(), mtime=0))
        rebind(prepared, "archive")
    if change != "oversized":
        replace_archive(prepared, records)
    with pytest.raises(ValueError):
        verify(prepared)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory-link", "source-copy"])
def test_unsafe_sources_fail_before_output_creation(source, tmp_path, options, kind):
    if kind == "symlink":
        (source / "linked").symlink_to(source / "response.bin")
    elif kind == "hardlink":
        os.link(source / "response.bin", source / "linked")
    elif kind == "directory-link":
        (source / "linked").symlink_to(source / "nested", target_is_directory=True)
    else:
        folder = source / "checkpoint-source"
        folder.mkdir()
        (folder / "source.py").write_text("source belongs to the declared commit")
    output = tmp_path / "assets"
    with pytest.raises(ValueError):
        assets.prepare(source, output, **options)
    assert not output.exists()
    assert (source / "response.bin").read_bytes() == bytes(range(256))


def test_prepare_rejects_nested_output_and_preserves_existing_output(source, tmp_path, options):
    with pytest.raises(ValueError, match="outside"):
        assets.prepare(source, source / "assets", **options)
    output = tmp_path / "assets"
    output.mkdir()
    (output / "retained").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        assets.prepare(source, output, **options)
    assert (output / "retained").read_bytes() == b"keep"


def test_unavailable_source_commit_cannot_be_packaged(source, tmp_path, options):
    options["source_commit"] = "0" * 40
    with pytest.raises(subprocess.CalledProcessError):
        assets.prepare(source, tmp_path / "assets", **options)
    assert not (tmp_path / "assets").exists()


def test_change_while_archiving_never_produces_a_complete_index(
    source, tmp_path, options, monkeypatch
):
    original = tarfile.TarFile.addfile

    def changing(archive, info, stream):
        original(archive, info, stream)
        if info.name == "response.bin":
            (source / info.name).write_bytes(b"!" * info.size)

    monkeypatch.setattr(tarfile.TarFile, "addfile", changing)
    output = tmp_path / "assets"
    with pytest.raises(ValueError, match="changed"):
        assets.prepare(source, output, **options)
    assert not (output / "index.json").exists()


def test_failed_final_verification_keeps_only_pending_metadata(
    source, tmp_path, options, monkeypatch
):
    def failed(*args):
        raise ValueError("authored verification failure")

    monkeypatch.setattr(assets, "verify", failed)
    output = tmp_path / "assets"
    with pytest.raises(ValueError, match="authored verification"):
        assets.prepare(source, output, **options)
    assert not (output / "index.json").exists()
    assert (output / "index.pending.json").exists()


def test_duplicate_inventory_keys_and_expansion_limits_fail(prepared, monkeypatch):
    path = prepared / "files.json.gz"
    original = path.read_bytes()
    path.write_bytes(gzip.compress(b'{"schema_version":1,"files":{},"files":{}}', mtime=0))
    rebind(prepared, "inventory")
    with pytest.raises(ValueError, match="Duplicate JSON"):
        verify(prepared)
    path.write_bytes(original)
    rebind(prepared, "inventory")
    monkeypatch.setattr(assets, "MAX_INVENTORY_BYTES", 8)
    with pytest.raises(ValueError, match="Expanded inventory"):
        verify(prepared)


def checkpoint(tmp_path):
    folder = tmp_path / PREFIX
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("Authored metadata checkpoint.\n")
    (folder / "acceptance.json").write_text('{"fixture_only": true}\n')
    return folder


def paths(root):
    return [
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()
    ]


def test_new_metadata_checkpoint_passes_and_exact_size_boundary_fails(tmp_path):
    folder = checkpoint(tmp_path)
    result = assets.check_policy(tmp_path, paths(tmp_path), EMPTY_POLICY)
    assert result["checkpoints"] == 1 and result["git_bytes"] < 1000
    (folder / "padding.txt").write_bytes(b"x" * (assets.GIT_CHECKPOINT_LIMIT - result["git_bytes"]))
    with pytest.raises(ValueError, match="byte allowance"):
        assets.check_policy(tmp_path, paths(tmp_path), EMPTY_POLICY)


@pytest.mark.parametrize("name", ["artifacts.tar.gz", "TRACE.ZIP", "files.json.gz"])
def test_new_compressed_git_blobs_are_refused(tmp_path, name):
    folder = checkpoint(tmp_path)
    (folder / name).write_bytes(b"archive")
    with pytest.raises(ValueError, match="outside Git"):
        assets.check_policy(tmp_path, paths(tmp_path), EMPTY_POLICY)


def test_renaming_an_archive_to_text_cannot_bypass_retention(tmp_path):
    folder = checkpoint(tmp_path)
    (folder / "trace.txt").write_bytes(gzip.compress(b"fixture trace", mtime=0))
    with pytest.raises(ValueError, match="outside Git"):
        assets.check_policy(tmp_path, paths(tmp_path), EMPTY_POLICY)


def test_legacy_archive_allowance_is_bound_to_exact_bytes(tmp_path):
    folder = checkpoint(tmp_path)
    archive = folder / "artifacts.tar.gz"
    archive.write_bytes(b"original")
    policy = {
        **EMPTY_POLICY,
        "legacy_archives": {
            archive.relative_to(tmp_path).as_posix(): hashlib.sha256(b"original").hexdigest(),
        },
    }
    assert assets.check_policy(tmp_path, paths(tmp_path), policy)["checkpoints"] == 1
    archive.write_bytes(b"modified")
    with pytest.raises(ValueError, match="outside Git"):
        assets.check_policy(tmp_path, paths(tmp_path), policy)


def test_staged_legacy_deletion_requires_a_verified_reference(tmp_path):
    folder = checkpoint(tmp_path)
    name = (folder / "artifacts.tar.gz").relative_to(tmp_path).as_posix()
    policy = {**EMPTY_POLICY, "legacy_archives": {name: hashlib.sha256(b"old").hexdigest()}}
    with pytest.raises(ValueError, match="missing without"):
        assets.check_policy(tmp_path, paths(tmp_path), policy)


@pytest.fixture
def historical(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    folder = checkpoint(repo)
    originals = {
        PREFIX + "/artifacts.tar.gz": gzip.compress(b"Original binary evidence\0", mtime=0),
        PREFIX + "/pytest.xml": b'<testsuite hostname="historical-host"/>',
        PREFIX + "/audit.py": b"raise RuntimeError('must not execute original code')\n",
    }
    for name, raw in originals.items():
        (repo / name).write_bytes(raw)
    (folder / "audit.py").chmod(0o755)

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=repo,
            stderr=subprocess.STDOUT,
        )

    git("init", "-q")
    git("add", "-f", ".")
    git("commit", "-qm", "Original evidence")
    commit = git("rev-parse", "HEAD").decode().strip()
    records = {
        name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        for name, raw in originals.items()
    }
    policy = {
        "schema_version": 2,
        "baseline_commit": commit,
        "legacy_checkpoint_bytes": {PREFIX: 10000},
        "legacy_archives": {
            PREFIX + "/artifacts.tar.gz": records[PREFIX + "/artifacts.tar.gz"]["sha256"]
        },
        "historical_git_files": records,
    }
    git("rm", "--", *originals)
    git("commit", "-qm", "Reference original evidence")
    return repo, policy, originals, git


def test_historical_restore_preserves_all_bytes_and_never_executes(historical, tmp_path):
    repo, policy, originals, _ = historical
    result = assets.check_policy(repo, paths(repo), policy)
    assert result["historical_git_files"] == 3
    output = tmp_path / "restored"
    restored = assets.restore_historical(repo, output, policy)
    assert restored["verified"] and not restored["executed"] and not restored["sanitized"]
    assert restored["bytes"] == sum(map(len, originals.values()))
    assert output.stat().st_mode & 0o777 == 0o700
    for name, raw in originals.items():
        assert (output / name).read_bytes() == raw
        assert (output / name).stat().st_mode & 0o777 == 0o600
        assert not (repo / name).exists()
    manifest = json.loads((output / "restoration.json").read_text())
    assert manifest["files"] == policy["historical_git_files"]
    assert manifest["source_commit"] == policy["baseline_commit"]


def test_historical_reference_cannot_hide_dropped_or_reintroduced_originals(historical):
    repo, policy, originals, _ = historical
    name = PREFIX + "/artifacts.tar.gz"
    record = policy["historical_git_files"].pop(name)
    with pytest.raises(ValueError, match="missing without"):
        assets.check_policy(repo, paths(repo), policy)
    policy["historical_git_files"][name] = record
    (repo / name).write_bytes(originals[name])
    with pytest.raises(ValueError, match="outside the checkout"):
        assets.check_policy(repo, paths(repo), policy)


@pytest.mark.parametrize("changed", ["commit", "noncommit", "hash", "size", "blob"])
def test_historical_missing_or_corrupt_data_never_completes(historical, tmp_path, changed):
    repo, policy, _, git = historical
    name = PREFIX + "/pytest.xml"
    record = policy["historical_git_files"][name]
    if changed == "commit":
        policy["baseline_commit"] = "f" * 40
    elif changed == "noncommit":
        policy["baseline_commit"] = (
            git("rev-parse", policy["baseline_commit"] + ":" + name).decode().strip()
        )
    elif changed == "hash":
        record["sha256"] = "0" * 64
    elif changed == "size":
        record["bytes"] -= 1
    else:
        oid = git("rev-parse", policy["baseline_commit"] + ":" + name).decode().strip()
        blob = repo / ".git/objects" / oid[:2] / oid[2:]
        blob.chmod(0o600)
        blob.write_bytes(b"corrupt object")
    with pytest.raises(ValueError, match="Historical original"):
        assets.check_policy(repo, paths(repo), policy)
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="Historical original"):
        assets.restore_historical(repo, output, policy, [name])
    assert not (output / "restoration.json").exists()


def test_historical_links_are_not_restored(historical, tmp_path):
    repo, policy, _, git = historical
    name = PREFIX + "/link.txt"
    (repo / name).symlink_to("pytest.xml")
    git("add", "-f", name)
    git("commit", "-qm", "Unacceptable linked evidence")
    policy["baseline_commit"] = git("rev-parse", "HEAD").decode().strip()
    policy["historical_git_files"][name] = {
        "sha256": hashlib.sha256(b"pytest.xml").hexdigest(),
        "bytes": len(b"pytest.xml"),
    }
    with pytest.raises(ValueError, match="regular Git file"):
        assets.restore_historical(repo, tmp_path / "linked", policy, [name])


def test_historical_restore_refuses_existing_checkout_or_unknown_destinations(historical, tmp_path):
    repo, policy, _, _ = historical
    output = tmp_path / "existing"
    output.mkdir()
    (output / "sentinel").write_text("unchanged")
    with pytest.raises(FileExistsError):
        assets.restore_historical(repo, output, policy)
    assert (output / "sentinel").read_text() == "unchanged"
    with pytest.raises(ValueError, match="outside the repository"):
        assets.restore_historical(repo, repo / "restore", policy)
    with pytest.raises(ValueError, match="Select recorded"):
        assets.restore_historical(repo, tmp_path / "unknown", policy, ["../escape"])
    assert not (tmp_path / "unknown").exists()


def test_historical_migration_does_not_reclaim_the_old_git_byte_allowance(historical):
    repo, policy, _, _ = historical
    current = assets.check_policy(repo, paths(repo), policy)
    migrated = current["historical_git_bytes"]
    padding = policy["legacy_checkpoint_bytes"][PREFIX] - migrated
    padding += assets.GIT_CHECKPOINT_LIMIT - current["git_bytes"]
    (repo / PREFIX / "padding.txt").write_bytes(b"x" * padding)
    with pytest.raises(ValueError, match="byte allowance"):
        assets.check_policy(repo, paths(repo), policy)


@pytest.mark.parametrize("invalid", ["commit_type", "path", "size", "archive_hash"])
def test_historical_reference_declarations_fail_before_restoring(historical, tmp_path, invalid):
    repo, policy, _, _ = historical
    name = PREFIX + "/artifacts.tar.gz"
    if invalid == "commit_type":
        policy["baseline_commit"] = 1
    elif invalid == "path":
        policy["historical_git_files"]["../escape"] = policy["historical_git_files"].pop(name)
    elif invalid == "size":
        policy["historical_git_files"][name]["bytes"] = True
    else:
        policy["historical_git_files"][name]["sha256"] = "0" * 64
    output = tmp_path / "invalid"
    with pytest.raises(ValueError):
        assets.restore_historical(repo, output, policy)
    assert not output.exists()


def test_shallow_checkout_requires_explicit_history_fetch(historical, tmp_path):
    repo, policy, _, _ = historical
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)], check=True)
    with pytest.raises(ValueError, match="fetch the recorded"):
        assets.check_policy(shallow, paths(shallow), policy)
    subprocess.run(
        ["git", "fetch", "-q", "origin", policy["baseline_commit"]], cwd=shallow, check=True
    )
    assert assets.check_policy(shallow, paths(shallow), policy)["historical_git_files"] == 3


def test_historical_cli_restores_only_selected_original(historical, tmp_path):
    repo, policy, originals, _ = historical
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy))
    name = PREFIX + "/pytest.xml"
    output = tmp_path / "cli-restored"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_harness.evaluation.evidence_assets",
            "restore-legacy",
            "--repo",
            str(repo),
            "--policy",
            str(policy_path),
            "--out",
            str(output),
            "--file",
            name,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["files"] == 1
    assert (output / name).read_bytes() == originals[name]
    assert not (output / PREFIX / "audit.py").exists()


def test_missing_readme_and_root_level_archive_cannot_bypass_policy(tmp_path):
    folder = checkpoint(tmp_path)
    (folder / "README.md").unlink()
    with pytest.raises(ValueError, match="README"):
        assets.check_policy(tmp_path, paths(tmp_path), EMPTY_POLICY)
    (folder / "README.md").write_text("Restored")
    (folder.parent / "archive.gz").write_bytes(b"bytes")
    with pytest.raises(ValueError, match="dedicated checkpoint"):
        assets.check_policy(tmp_path, paths(tmp_path), EMPTY_POLICY)


def test_cli_prepares_and_verifies_without_executing_or_uploading(source, tmp_path, options):
    output = tmp_path / "assets"
    command = [sys.executable, "-m", "research_harness.evaluation.evidence_assets"]
    completed = subprocess.run(
        [
            *command,
            "prepare",
            str(source),
            str(output),
            "--repository",
            options["repository"],
            "--source-commit",
            options["source_commit"],
            "--tag",
            options["tag"],
            "--source-repository",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["availability"] == "prepared"
    completed = subprocess.run(
        [
            *command,
            "verify",
            str(output / "index.json"),
            str(output / "artifacts.tar.gz"),
            str(output / "files.json.gz"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["file_count"] == 3


def test_policy_cli_checks_untracked_and_force_added_archives(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    folder = checkpoint(tmp_path)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(EMPTY_POLICY))
    command = [
        sys.executable,
        "-m",
        "research_harness.evaluation.evidence_assets",
        "check-policy",
        "--repo",
        str(tmp_path),
        "--policy",
        str(policy),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["checkpoints"] == 1
    (tmp_path / ".gitignore").write_text("*.gz\n")
    (folder / "artifacts.tar.gz").write_bytes(gzip.compress(b"trace", mtime=0))
    subprocess.run(["git", "add", "-f", str(folder / "artifacts.tar.gz")], cwd=tmp_path, check=True)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and "outside Git" in result.stderr
