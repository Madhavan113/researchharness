"""Prepare private review inputs from one fixed historical evidence checkpoint.

This recipe never checks out or executes archived code. Compressed originals
remain in Git; their decoded bytes and container/member provenance enter a new
private directory for the separate portable review exporter.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

COMMIT = "b58bf44a84407d1069039499411e53aaaede5968"
ROOTS = ("examples/evaluation/evidence", "examples/omnigent/evidence")
MAX_FILES = 100_000
MAX_BYTES = 16 * 1024**3


def digest(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def relative(name):
    path = PurePosixPath(name)
    if (
        not name
        or name == "."
        or path.is_absolute()
        or path.as_posix() != name
        or ".." in path.parts
        or "\\" in name
        or "\0" in name
    ):
        raise ValueError("Unsafe historical member name")
    return name


def prepare(repo, output):
    repo, output = repo.resolve(), output.resolve()
    if output.is_relative_to(repo):
        raise ValueError("Use a new private directory outside the checkout")

    def git(*args):
        return subprocess.check_output(["git", "--no-replace-objects", *args], cwd=repo)

    policy = json.loads((repo / "examples/evaluation/evidence-policy.json").read_bytes())
    if policy["baseline_commit"] != COMMIT:
        raise ValueError("This recipe is bound to the recorded historical baseline")
    blobs, originals = {}, {}
    for entry in git("ls-tree", "-rz", "--long", COMMIT, "--", *ROOTS).split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid, size = metadata.split()
        name = relative(name.decode())
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ValueError("Historical evidence must contain only regular Git files")
        raw = git("cat-file", "blob", oid.decode())
        if len(raw) != int(size):
            raise ValueError("Historical Git blob size differs")
        blobs[name] = raw
        originals[name] = {**digest(raw), "git_blob": oid.decode()}
    if len(blobs) != 150 or sum(map(len, blobs.values())) != 27_209_764:
        raise ValueError("Historical baseline membership or byte total differs")

    output.mkdir(mode=0o700, exist_ok=False)
    files, containers, nested, total = {}, {}, {}, 0

    def write(name, raw, origin, depth=0):
        nonlocal total
        relative(name)
        if depth > 8:
            raise ValueError("Nested historical archive depth exceeds the review bound")
        if raw.startswith(b"\x1f\x8b"):
            if name in nested or len(nested) + len(files) >= MAX_FILES:
                raise ValueError("Duplicate nested container or exceeded input count bound")
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                decoded = stream.read(MAX_BYTES - total + 1)
            if len(decoded) > MAX_BYTES - total:
                raise ValueError("Nested historical archive exceeds the byte bound")
            entries, seen = [], set()
            nested[name] = {**digest(raw), **origin, "kind": "tar-gzip", "entries": entries}
            with tarfile.open(fileobj=io.BytesIO(decoded), mode="r:") as archive:
                for member in archive:
                    normalized = member.name.removeprefix("./")
                    if member.isdir() and normalized == ".":
                        normalized = ""
                    elif normalized:
                        relative(normalized)
                    if normalized in seen or not (member.isfile() or member.isdir()):
                        raise ValueError("Unsafe or duplicate nested historical member")
                    seen.add(normalized)
                    entry = {"member": member.name, "original_mode": member.mode}
                    if member.isdir():
                        entries.append({**entry, "kind": "directory"})
                        continue
                    if not normalized or not 0 <= member.size <= MAX_BYTES - total:
                        raise ValueError("Invalid or oversized nested historical file")
                    with archive.extractfile(member) as stream:
                        data = stream.read(member.size + 1)
                    if len(data) != member.size:
                        raise ValueError("Nested historical member is incomplete")
                    entries.append({**entry, **digest(data), "kind": "file"})
                    write(
                        name + ".members/" + normalized,
                        data,
                        {"parent_input": name, "member": member.name},
                        depth + 1,
                    )
            return
        total += len(raw)
        if name in files or len(files) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError("Duplicate historical input or exceeded complete-input bound")
        target = output / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(
            os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as stream:
            stream.write(raw)
        files[name] = {**digest(raw), **origin}

    for name, raw in sorted(blobs.items()):
        expected = policy["legacy_archives"].get(name)
        if expected is not None and digest(raw)["sha256"] != expected:
            raise ValueError("Original compressed hash differs from the fixed catalog")
        if name.endswith(".tar.gz"):
            index = json.loads(blobs[str(PurePosixPath(name).parent / "index.json")])
            if index["archive_sha256"] != digest(raw)["sha256"]:
                raise ValueError("Original archive differs from its original member index")
            seen, expanded = set(), 0
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                for member in archive:
                    relative(member.name)
                    if not member.isfile() or member.name in seen:
                        raise ValueError(
                            "Original archive contains nonregular or duplicate members"
                        )
                    if (
                        member.name not in index["files"]
                        or not 0 <= member.size <= MAX_BYTES - total
                    ):
                        raise ValueError("Unexpected or oversized historical member")
                    with archive.extractfile(member) as stream:
                        data = stream.read(member.size + 1)
                    if (
                        len(data) != member.size
                        or digest(data)["sha256"] != index["files"][member.name]
                    ):
                        raise ValueError("Original member hash or length differs")
                    write(
                        name + ".members/" + member.name,
                        data,
                        {"git_path": name, "member": member.name, "original_mode": member.mode},
                    )
                    expanded += len(data)
                    seen.add(member.name)
            if seen != set(index["files"]):
                raise ValueError("Original archive membership is incomplete")
            containers[name] = {"kind": "tar-gzip", "files": len(seen), "expanded_bytes": expanded}
        elif name.endswith(".gz"):
            if expected is None or not name.endswith("/model-requests.json.gz"):
                raise ValueError("Unexpected compressed historical file")
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                data = stream.read(MAX_BYTES - total + 1)
            if len(data) > MAX_BYTES - total or not isinstance(json.loads(data), list):
                raise ValueError("Invalid or oversized historical JSON request capture")
            write(
                name + ".decoded/model-requests.json", data, {"git_path": name, "encoding": "gzip"}
            )
            containers[name] = {"kind": "gzip-json", "files": 1, "expanded_bytes": len(data)}
        else:
            write(name, raw, {"git_path": name})
    if set(containers) != set(policy["legacy_archives"]):
        raise ValueError("Compressed historical baseline coverage is incomplete")
    provenance = {
        "kind": "research-harness-historical-review-inputs-v1",
        "git_commit": COMMIT,
        "originals": originals,
        "containers": containers,
        "nested_containers": nested,
        "files": files,
        "payload_bytes": total,
        "executed": False,
        "sanitized": False,
    }
    for name, record in files.items():
        if digest((output / name).read_bytes()) != {
            key: record[key] for key in ("sha256", "bytes")
        }:
            raise ValueError("Prepared original changed during verification")
    with (output / "historical-source.json").open("x") as stream:
        json.dump(provenance, stream, sort_keys=True, indent=2)
        stream.write("\n")
    (output / "historical-source.json").chmod(0o600)
    return {
        "original_files": len(originals),
        "containers": len(containers),
        "nested_containers": len(nested),
        "payload_files": len(files),
        "payload_bytes": total,
        "executed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.out), indent=2))
