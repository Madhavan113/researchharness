from __future__ import annotations

import os
import stat
import time
from threading import Event, Thread

import pytest

from research_harness import verification as module
from research_harness.verification import VerificationMemo


def settled(**kwargs):
    return VerificationMemo(clock_ns=lambda: time.time_ns() + 10_000_000_000, **kwargs)


def tree(root):
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("linked root")
    result = {}
    for path in root.iterdir():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("linked file")
        result[path.name] = path.read_bytes().hex()
    return result


def test_verified_results_are_reused_but_cannot_be_mutated(tmp_path):
    file = tmp_path / "artifact"
    file.write_text("original")
    calls = []
    memo = settled()

    def verify():
        calls.append(1)
        return {"files": tree(tmp_path), "nested": [1]}

    first = memo.verify("context", [tmp_path], verify)
    first["files"].clear()
    first["nested"].append(2)
    second = memo.verify("context", [tmp_path], verify)
    assert second == {"files": tree(tmp_path), "nested": [1]}
    second["nested"].clear()
    assert memo.verify("context", [tmp_path], verify)["nested"] == [1]
    assert len(calls) == (1 if os.name == "posix" else 3)
    memo.verify("different-controls", [tmp_path], verify)
    assert len(calls) == (2 if os.name == "posix" else 4)


@pytest.mark.parametrize("change", ["restored-mtime", "replacement", "addition", "removal", "mode"])
def test_file_changes_invalidate_a_settled_proof(tmp_path, change):
    file = tmp_path / "artifact"
    file.write_text("original")
    previous = file.stat()
    memo, calls = settled(), []

    def verify():
        calls.append(1)
        return tree(tmp_path)

    memo.verify("context", [tmp_path], verify)
    if change == "restored-mtime":
        file.write_text("modified")
        os.utime(file, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        assert file.stat().st_mtime_ns == previous.st_mtime_ns
    elif change == "replacement":
        replacement = tmp_path / "replacement"
        replacement.write_text("modified")
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        replacement.replace(file)
    elif change == "addition":
        (tmp_path / "added").write_text("new bytes")
    elif change == "removal":
        file.unlink()
    else:
        file.chmod(0o400)
    assert memo.verify("context", [tmp_path], verify) == tree(tmp_path)
    assert len(calls) == 2


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "root-symlink"])
def test_links_never_reuse_an_earlier_valid_result(tmp_path, kind):
    root = tmp_path / "root"
    root.mkdir()
    (root / "artifact").write_text("original")
    memo = settled()
    memo.verify("context", [root], lambda: tree(root))
    target = tmp_path / "foreign"
    target.write_text("foreign bytes")
    if kind == "root-symlink":
        root.rename(tmp_path / "saved")
        root.symlink_to(tmp_path / "saved", target_is_directory=True)
    else:
        (root / "artifact").unlink()
        if kind == "symlink":
            (root / "artifact").symlink_to(target)
        else:
            os.link(target, root / "artifact")
    with pytest.raises(ValueError, match="linked"):
        memo.verify("context", [root], lambda: tree(root))
    assert target.read_text() == "foreign bytes"


def test_recent_coarse_metadata_and_failed_results_are_never_reused(tmp_path, monkeypatch):
    file = tmp_path / "artifact"
    file.write_text("original")
    now = 10_000_000_000
    monkeypatch.setattr(module, "_snapshot", lambda roots: ((("coarse",),), now))
    memo = VerificationMemo(clock_ns=lambda: now + 1)
    assert memo.verify("context", [file], file.read_text) == "original"
    file.write_text("modified")
    assert memo.verify("context", [file], file.read_text) == "modified"
    memo._clock_ns = lambda: now + 3_000_000_000
    calls = []

    def rejected():
        calls.append(1)
        return {"status": "invalid"}

    for _ in range(2):
        assert memo.verify("invalid", [file], rejected, cache_if=lambda r: False) == {
            "status": "invalid"
        }
    assert len(calls) == 2


def test_change_during_full_verification_is_not_cached(tmp_path):
    file = tmp_path / "artifact"
    file.write_text("original")
    memo = settled()

    def racing():
        result = file.read_text()
        file.write_text("modified")
        return result

    if os.name == "posix":
        with pytest.raises(ValueError, match="changed during"):
            memo.verify("context", [file], racing)
    else:
        assert memo.verify("context", [file], racing) == "original"
    assert memo.verify("context", [file], file.read_text) == "modified"


@pytest.mark.parametrize("limits", [{"max_entries": 1}, {"max_bytes": 1}, {"max_bytes": 0}])
def test_capacity_limits_fall_back_to_full_verification(tmp_path, limits):
    file = tmp_path / "artifact"
    file.write_text("bytes")
    memo, calls = settled(**limits), []

    def verify():
        calls.append(1)
        return file.read_text()

    for key in ("first", "second", "first"):
        assert memo.verify(key, [file], verify) == "bytes"
    assert len(calls) == 3


def test_oversized_metadata_scan_preserves_the_original_verifier(tmp_path, monkeypatch):
    (tmp_path / "artifact").write_text("bytes")
    monkeypatch.setattr(module, "_MAX_SCAN_ENTRIES", 1)
    memo, calls = settled(), []

    def verify():
        calls.append(1)
        return tree(tmp_path)

    assert memo.verify("context", [tmp_path], verify) == memo.verify("context", [tmp_path], verify)
    assert len(calls) == 2


def test_clear_cannot_be_undone_by_an_inflight_verification(tmp_path):
    file = tmp_path / "artifact"
    file.write_text("bytes")
    entered, release = Event(), Event()
    memo, calls, errors = settled(), [], []

    def verify():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return file.read_text()

    def worker():
        try:
            memo.verify("context", [file], verify)
        except Exception as exc:
            errors.append(exc)

    thread = Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(5)
        memo.clear()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert memo.verify("context", [file], verify) == "bytes"
    assert len(calls) == 2
