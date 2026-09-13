"""Prepare, verify or restore checkpoint evidence without uploading or executing it."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

KIND = "research-harness-evidence-release-v1"
MAX_FILES = 100_000
MAX_BYTES = 16 * 1024**3
MAX_INVENTORY_BYTES = 32 * 1024**2
GIT_CHECKPOINT_LIMIT = 1_000_000
EVIDENCE_ROOTS = ("examples/evaluation/evidence", "examples/omnigent/evidence")
ARCHIVE_SUFFIXES = (".gz", ".zip", ".tar", ".tgz", ".xz", ".bz2", ".zst", ".7z")


def _json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def _relative(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or len(name.encode()) > 4096
        or "\\" in name
        or ":" in name
        or any(not char.isprintable() for char in name)
        or PurePosixPath(name).is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise ValueError("Unsafe evidence member path")
    return name


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _regular(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Evidence requires regular unlinked files")
    return info


@contextmanager
def _open(path: Path):
    before = _regular(path)
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            raise ValueError("Evidence changed while opening")
        yield stream
        if _identity(os.fstat(stream.fileno())) != _identity(before) or _identity(
            path.lstat()
        ) != _identity(before):
            raise ValueError("Evidence changed while reading")


def _digest(stream):
    sha, size = hashlib.sha256(), 0
    while chunk := stream.read(1024 * 1024):
        sha.update(chunk)
        size += len(chunk)
        if size > MAX_BYTES:
            raise ValueError("Evidence exceeds the configured byte limit")
    return {"sha256": sha.hexdigest(), "bytes": size}


def _file_record(path):
    with _open(path) as stream:
        return {"name": path.name, **_digest(stream)}


def _compressed(path):
    if path.name.lower().endswith(ARCHIVE_SUFFIXES):
        return True
    with _open(path) as stream:
        prefix = stream.read(512)
    return (
        prefix.startswith(
            (
                b"\x1f\x8b",
                b"PK\x03\x04",
                b"PK\x05\x06",
                b"PK\x07\x08",
                b"BZh",
                b"\xfd7zXZ\x00",
                b"\x28\xb5\x2f\xfd",
                b"7z\xbc\xaf\x27\x1c",
            )
        )
        or prefix[257:262] == b"ustar"
    )


def _tree(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Use a dedicated unlinked evidence directory")

    def fail(error):
        raise error

    files, total, entries = {}, 0, 0
    for parent, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        entries += len(dirs) + len(names)
        if entries > MAX_FILES:
            raise ValueError("Complete evidence exceeds the directory entry limit")
        for name in dirs:
            if (Path(parent) / name).is_symlink():
                raise ValueError("Evidence directory links are forbidden")
        for name in names:
            path = Path(parent) / name
            relative = _relative(path.relative_to(root).as_posix())
            if relative.split("/")[0] == "checkpoint-source":
                raise ValueError("Use the source commit instead of redundant checkpoint-source")
            info = _regular(path)
            total += info.st_size
            files[relative] = _identity(info)
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise ValueError("Complete evidence exceeds file/byte limits")
    if not files:
        raise ValueError("Evidence directory is empty")
    return dict(sorted(files.items()))


def _location(repository, commit, tag):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repository must be owner/name")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Source commit must be a complete Git SHA-1")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("Use a simple immutable checkpoint release tag")
    return f"https://github.com/{repository}/releases/download/{tag}"


def prepare(
    source: Path,
    output: Path,
    *,
    repository: str,
    source_commit: str,
    tag: str,
    source_repository: Path | None = None,
):
    """Package every file of an explicitly prepared export; retain inputs unchanged."""
    location = _location(repository, source_commit, tag)
    subprocess.run(
        ["git", "cat-file", "-e", source_commit + "^{commit}"],
        cwd=source_repository,
        check=True,
        capture_output=True,
    )
    source, output = source.absolute(), output.absolute()
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError("Asset output must be outside the evidence input")
    before = _tree(source)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    archive_path = output / "artifacts.tar.gz"
    files = {}
    with archive_path.open("xb") as target:
        with gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for name, identity in before.items():
                    path = source / name
                    with _open(path) as stream:
                        if _identity(os.fstat(stream.fileno())) != identity:
                            raise ValueError("Evidence changed after inventory")
                        info = tarfile.TarInfo(name)
                        info.size = identity[4]
                        info.mode = 0o755 if identity[2] & stat.S_IXUSR else 0o644
                        # TarInfo defaults normalize uid/gid, names and mtime.
                        archive.addfile(info, stream)
                        stream.seek(0)
                        files[name] = {**_digest(stream), "mode": info.mode}
    if _tree(source) != before:
        raise ValueError("Evidence tree changed during packaging")
    inventory = _encoded({"schema_version": 1, "files": files})
    if len(inventory) > MAX_INVENTORY_BYTES:
        raise ValueError("Complete inventory exceeds the byte limit")
    inventory_path = output / "files.json.gz"
    with inventory_path.open("xb") as stream:
        with gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
            compressed.write(inventory)
    index = {
        "kind": KIND,
        "repository": repository,
        "source_commit": source_commit,
        "release_tag": tag,
        "availability": "prepared",
        "archive": _file_record(archive_path),
        "inventory": _file_record(inventory_path),
        "file_count": len(files),
        "uncompressed_bytes": sum(record["bytes"] for record in files.values()),
    }
    for key in ("archive", "inventory"):
        index[key]["url"] = f"{location}/{index[key]['name']}"
    pending = output / "index.pending.json"
    pending.write_bytes(_encoded(index))
    verify(pending, archive_path, inventory_path)
    # Publish the completion record last; partial preparation is not a checkpoint.
    pending.rename(output / "index.json")
    return index


def _record(record):
    if (
        not isinstance(record, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
        or type(record.get("bytes")) is not int
        or not 0 <= record["bytes"] <= MAX_BYTES
    ):
        raise ValueError("Invalid evidence size/hash record")


def load_index(path):
    with _open(path) as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Evidence index is too large")
    index = _json(raw)
    if not isinstance(index, dict) or index.get("kind") != KIND:
        raise ValueError("Unsupported evidence index")
    location = _location(index["repository"], index["source_commit"], index["release_tag"])
    if index.get("availability") not in {"prepared", "download_verified"}:
        raise ValueError("Invalid asset availability")
    for key, name in (("archive", "artifacts.tar.gz"), ("inventory", "files.json.gz")):
        _record(index[key])
        if index[key].get("name") != name or index[key].get("url") != f"{location}/{name}":
            raise ValueError("Evidence asset destination changed")
    if (
        type(index.get("file_count")) is not int
        or not 1 <= index["file_count"] <= MAX_FILES
        or type(index.get("uncompressed_bytes")) is not int
        or not 0 <= index["uncompressed_bytes"] <= MAX_BYTES
    ):
        raise ValueError("Invalid evidence inventory bounds")
    return index


@contextmanager
def _verified(path, record):
    with _open(path) as stream:
        if _digest(stream) != {key: record[key] for key in ("sha256", "bytes")}:
            raise ValueError("Evidence asset hash or size changed")
        stream.seek(0)
        yield stream


def verify(index_path: Path, archive_path: Path, inventory_path: Path):
    """Check compressed hashes and every member without extracting or executing files."""
    index = load_index(index_path)
    with _verified(inventory_path, index["inventory"]) as stream:
        with gzip.GzipFile(fileobj=stream, mode="rb") as compressed:
            raw = compressed.read(MAX_INVENTORY_BYTES + 1)
    if len(raw) > MAX_INVENTORY_BYTES:
        raise ValueError("Expanded inventory exceeds its byte limit")
    inventory = _json(raw)
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        raise ValueError("Unsupported evidence inventory")
    files = inventory.get("files")
    if not isinstance(files, dict) or len(files) != index["file_count"]:
        raise ValueError("Evidence file count differs")
    for name, record in files.items():
        _relative(name)
        _record(record)
        if type(record.get("mode")) is not int or record["mode"] not in {0o644, 0o755}:
            raise ValueError("Invalid evidence file mode")
    if sum(record["bytes"] for record in files.values()) != index["uncompressed_bytes"]:
        raise ValueError("Evidence total bytes differ")
    seen = set()
    with _verified(archive_path, index["archive"]) as stream:
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
            for member in archive:
                name = _relative(member.name)
                if not member.isfile() or member.issparse() or name in seen or name not in files:
                    raise ValueError("Unexpected, linked or duplicate evidence member")
                expected = files[name]
                if member.size != expected["bytes"] or member.mode != expected["mode"]:
                    raise ValueError("Evidence member size or mode changed")
                if member.uid or member.gid or member.uname or member.gname or member.mtime:
                    raise ValueError("Evidence archive contains unnormalized host metadata")
                with archive.extractfile(member) as payload:
                    if _digest(payload) != {key: expected[key] for key in ("sha256", "bytes")}:
                        raise ValueError("Evidence member hash changed")
                seen.add(name)
    if seen != set(files):
        raise ValueError("Evidence members are missing")
    return {"verified": True, "file_count": len(seen), "bytes": index["uncompressed_bytes"]}


def _historical_records(policy):
    if not isinstance(policy, dict):
        raise ValueError("Invalid historical Git reference policy")
    records = policy.get("historical_git_files", {})
    if records == {} and policy.get("schema_version") == 1:
        return {}
    if (
        type(policy.get("schema_version")) is not int
        or policy.get("schema_version") != 2
        or not isinstance(records, dict)
        or not 1 <= len(records) <= MAX_FILES
        or not isinstance(policy.get("baseline_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", policy["baseline_commit"])
    ):
        raise ValueError("Invalid historical Git reference policy")
    migrated = {}
    for name, record in records.items():
        _relative(name)
        prefix = next((item for item in EVIDENCE_ROOTS if name.startswith(item + "/")), None)
        if prefix is None:
            raise ValueError("Historical references must stay inside the evidence roots")
        relative = name[len(prefix) + 1 :]
        checkpoint = prefix + "/" + relative.split("/")[0]
        if checkpoint not in policy.get("legacy_checkpoint_bytes", {}):
            raise ValueError("Historical reference is not part of the fixed legacy baseline")
        _record(record)
        if set(record) != {"sha256", "bytes"}:
            raise ValueError("Historical references need only the original size and hash")
        expected = policy.get("legacy_archives", {}).get(name)
        if expected is not None and record["sha256"] != expected:
            raise ValueError("Historical reference differs from the original archive hash")
        migrated[checkpoint] = migrated.get(checkpoint, 0) + record["bytes"]
        if migrated[checkpoint] > policy["legacy_checkpoint_bytes"][checkpoint]:
            raise ValueError("Historical references exceed the original checkpoint size")
    if sum(record["bytes"] for record in records.values()) > MAX_BYTES:
        raise ValueError("Historical originals exceed the total byte limit")
    return records


def _historical_file(root, commit, name, expected, sink=None):
    """Read a regular blob from a pinned tree, without checkout, filters or execution."""
    command = ["git", "--no-replace-objects"]
    kind = subprocess.run(
        [*command, "cat-file", "-t", commit], cwd=root, capture_output=True, check=False
    )
    if kind.returncode or kind.stdout.strip() != b"commit":
        raise ValueError("Historical original unavailable; fetch the recorded baseline commit")
    entry = subprocess.run(
        [*command, "ls-tree", "--full-tree", "-z", commit, "--", name],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if entry.returncode or not entry.stdout.endswith(b"\0"):
        raise ValueError("Historical original unavailable; fetch the recorded baseline commit")
    metadata, _, path = entry.stdout[:-1].partition(b"\t")
    fields = metadata.split()
    if (
        len(fields) != 3
        or fields[0] not in {b"100644", b"100755"}
        or fields[1] != b"blob"
        or path != name.encode()
        or not re.fullmatch(rb"[0-9a-f]{40}", fields[2])
    ):
        raise ValueError("Historical reference is not one exact regular Git file")
    process = subprocess.Popen(
        [*command, "cat-file", "blob", fields[2].decode()],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    digest, size = hashlib.sha256(), 0
    try:
        while chunk := process.stdout.read(1024**2):
            size += len(chunk)
            if size > expected["bytes"]:
                raise ValueError("Historical original exceeds its recorded size")
            digest.update(chunk)
            if sink is not None:
                sink.write(chunk)
        if (
            process.wait(timeout=10)
            or {
                "bytes": size,
                "sha256": digest.hexdigest(),
            }
            != expected
        ):
            raise ValueError("Historical original differs from its recorded size or hash")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def restore_historical(root: Path, output: Path, policy: dict, names: list[str] | None = None):
    """Restore exact historical data into a fresh private tree; never run its contents."""
    records = _historical_records(policy)
    selected = sorted(records if names is None else set(names))
    if not selected or any(name not in records for name in selected):
        raise ValueError("Select recorded historical files")
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError("Restore historical originals outside the repository checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in selected:
        target = output / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as sink:
            _historical_file(root, policy["baseline_commit"], name, records[name], sink)
    before = _tree(output)
    if set(before) != set(selected):
        raise ValueError("Restored historical files differ from the recorded originals")
    for name in selected:
        actual = _file_record(output / name)
        if {key: actual[key] for key in ("sha256", "bytes")} != records[name]:
            raise ValueError("Restored historical files differ from the recorded originals")
    if _tree(output) != before:
        raise ValueError("Restored historical files changed during verification")
    summary = {
        "kind": "research-harness-historical-originals-v1",
        "source_commit": policy["baseline_commit"],
        "files": {name: records[name] for name in selected},
        "executed": False,
        "sanitized": False,
    }
    pending = output / "restoration.pending.json"
    pending.write_bytes(_encoded(summary))
    pending.rename(output / "restoration.json")
    return {
        "verified": True,
        "files": len(selected),
        "bytes": sum(records[name]["bytes"] for name in selected),
        "source_commit": policy["baseline_commit"],
        "executed": False,
        "sanitized": False,
    }


def check_policy(root: Path, paths: list[str], policy: dict):
    """Bound new Git checkpoints and pin every grandfathered compressed blob."""
    if (
        not isinstance(policy, dict)
        or type(policy.get("schema_version")) is not int
        or policy.get("schema_version") not in {1, 2}
        or not isinstance(policy.get("legacy_archives"), dict)
        or not isinstance(policy.get("legacy_checkpoint_bytes"), dict)
        or any(
            type(size) is not int or size < 0 for size in policy["legacy_checkpoint_bytes"].values()
        )
    ):
        raise ValueError("Invalid evidence retention policy")
    historical = _historical_records(policy)
    migrated = {}
    for name, record in historical.items():
        prefix = next(item for item in EVIDENCE_ROOTS if name.startswith(item + "/"))
        checkpoint = prefix + "/" + name[len(prefix) + 1 :].split("/")[0]
        migrated[checkpoint] = migrated.get(checkpoint, 0) + record["bytes"]
    totals, present = {}, set()
    for name in sorted(set(paths)):
        prefix = next((item for item in EVIDENCE_ROOTS if name.startswith(item + "/")), None)
        if prefix is None:
            continue
        relative = name[len(prefix) + 1 :]
        if "/" not in relative:
            if relative == "README.md":
                if _regular(root / name).st_size >= GIT_CHECKPOINT_LIMIT:
                    raise ValueError("Shared evidence README exceeds the Git byte allowance")
                continue
            if name not in policy["legacy_checkpoint_bytes"]:
                raise ValueError("Evidence files must live in a dedicated checkpoint directory")
            checkpoint = name
        else:
            checkpoint = prefix + "/" + relative.split("/")[0]
        _relative(name)
        path = root / name
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("Evidence directory links are forbidden")
        info = _regular(path)
        totals[checkpoint] = totals.get(checkpoint, 0) + info.st_size
        present.add(name)
        if name in historical:
            raise ValueError(f"Keep referenced historical originals outside the checkout: {name}")
        if _compressed(path):
            expected = policy["legacy_archives"].get(name)
            if expected is None or _file_record(path)["sha256"] != expected:
                raise ValueError(f"Store new or changed compressed evidence outside Git: {name}")
        if name.endswith("/index.json") and checkpoint not in policy["legacy_checkpoint_bytes"]:
            load_index(path)
    for checkpoint, size in totals.items():
        baseline = policy["legacy_checkpoint_bytes"].get(checkpoint, 0)
        if size >= baseline - migrated.get(checkpoint, 0) + GIT_CHECKPOINT_LIMIT:
            raise ValueError(f"Evidence checkpoint exceeds its Git byte allowance: {checkpoint}")
        if not baseline and checkpoint + "/README.md" not in present:
            raise ValueError(f"New evidence checkpoint needs a README: {checkpoint}")
    if set(policy["legacy_archives"]) - present - set(historical):
        raise ValueError("Legacy evidence is missing without a verified historical reference")
    for name, record in historical.items():
        _historical_file(root, policy["baseline_commit"], name, record)
    result = {"checkpoints": len(totals), "git_bytes": sum(totals.values())}
    if historical:
        result.update(
            historical_git_files=len(historical),
            historical_git_bytes=sum(record["bytes"] for record in historical.values()),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("source", type=Path)
    prepare_parser.add_argument("output", type=Path)
    prepare_parser.add_argument("--repository", required=True)
    prepare_parser.add_argument("--source-commit", required=True)
    prepare_parser.add_argument("--source-repository", type=Path, default=Path.cwd())
    prepare_parser.add_argument("--tag", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("index", type=Path)
    verify_parser.add_argument("archive", type=Path)
    verify_parser.add_argument("inventory", type=Path)
    policy_parser = commands.add_parser("check-policy")
    policy_parser.add_argument("--repo", type=Path, default=Path.cwd())
    policy_parser.add_argument("--policy", type=Path, required=True)
    restore_parser = commands.add_parser("restore-legacy")
    restore_parser.add_argument("--repo", type=Path, default=Path.cwd())
    restore_parser.add_argument("--policy", type=Path, required=True)
    restore_parser.add_argument("--out", type=Path, required=True)
    restore_parser.add_argument("--file", action="append", dest="files")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(
            args.source,
            args.output,
            repository=args.repository,
            source_commit=args.source_commit,
            tag=args.tag,
            source_repository=args.source_repository,
        )
    elif args.command == "verify":
        result = verify(args.index, args.archive, args.inventory)
    elif args.command == "restore-legacy":
        result = restore_historical(
            args.repo, args.out, _json(args.policy.read_bytes()), args.files
        )
    else:
        paths = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                cwd=args.repo,
            )
            .decode()
            .split("\0")
        )
        result = check_policy(
            args.repo, [name for name in paths if name], _json(args.policy.read_bytes())
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
