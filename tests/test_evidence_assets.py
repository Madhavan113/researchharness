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
