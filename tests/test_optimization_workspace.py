from __future__ import annotations

import base64
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from research_harness.optimization import workspace as module
from research_harness.optimization.workspace import ProposalWorkspace, WorkspaceConfig
from research_harness.strategies.sandbox import SandboxConfig
from research_harness.util import canonical_json, digest, write_json

IMAGE = "python@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"


def config(**values):
    return WorkspaceConfig(sandbox=SandboxConfig(image=IMAGE), **values)


def setup(tmp_path, settings=None):
    feedback = tmp_path / "feedback"
    feedback.mkdir()
    (feedback / "full.bin").write_bytes(bytes(range(256)) * 1000)
    nested = feedback / "nested"
    nested.mkdir()
    (nested / "private-mode.json").write_text('{"entire":"feedback"}')
    (nested / "private-mode.json").chmod(0o600)
    return ProposalWorkspace(feedback, tmp_path / "access", settings or config())


def write(workspace, path, code):
    result = workspace.call("write_file", {"path": path, "content": code})
    assert result["ok"], result


def test_copied_feedback_is_private_before_first_byte_and_removed_on_close(tmp_path, monkeypatch):
    public_temp = tmp_path / "shared-temp"
    public_temp.mkdir(mode=0o755)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(public_temp))
    original_open = Path.open
    observed = []
    output = tmp_path / "access"

    def check_copy(path, mode="r", *args, **kwargs):
        if mode == "xb" and path.name in {"full.bin", "private-mode.json"}:
            state = json.loads((output / "workspace.json").read_bytes())
            view = Path(state["feedback"])
            assert stat.S_IMODE(view.parent.stat().st_mode) == 0o700
            observed.append(path.name)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", check_copy)
    try:
        workspace = setup(tmp_path)
        parent = workspace.feedback.parent
        assert set(observed) == {"full.bin", "private-mode.json"}
        assert stat.S_IMODE(workspace.feedback.stat().st_mode) == 0o555
        assert (workspace.feedback / "full.bin").read_bytes() == bytes(range(256)) * 1000
        source = workspace.feedback_source / "nested/private-mode.json"
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        proof = workspace.close()
        assert proof["snapshot_valid"] and proof["feedback_view_removed"]
        assert not parent.exists() and source.read_text() == '{"entire":"feedback"}'
    finally:
        if (output / "workspace.json").exists():
            ProposalWorkspace.recover(output, config())


@pytest.mark.parametrize("point", ["parent", "view", "copy"])
def test_interrupted_feedback_preparation_removes_only_owned_copy(tmp_path, monkeypatch, point):
    original_mkdir, original_open = Path.mkdir, Path.open

    def interrupt_mkdir(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if (point == "parent" and path.name.startswith(".rh-proposer-feedback-")) or (
            point == "view"
            and path.name == "feedback"
            and path.parent.name.startswith(".rh-proposer-feedback-")
        ):
            raise OSError("Interrupted feedback preparation")
        return result

    def interrupt_copy(path, mode="r", *args, **kwargs):
        if point == "copy" and mode == "xb" and path.name == "private-mode.json":
            raise OSError("Interrupted feedback preparation")
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", interrupt_mkdir)
        patch.setattr(Path, "open", interrupt_copy)
        with pytest.raises(OSError, match="Interrupted feedback"):
            setup(tmp_path)
    output = tmp_path / "access"
    state = json.loads((output / "workspace.json").read_bytes())
    parent = Path(state["feedback"]).parent
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert state["status"] == "preparing" and not state["calls"]
    proof = ProposalWorkspace.recover(output, config())
    assert proof["closed"] and proof["quiescent"] and not proof["snapshot_valid"]
    assert proof["feedback_view_removed"] and not parent.exists()
    assert not list((output / "tools").iterdir())
    source = tmp_path / "feedback/nested/private-mode.json"
    assert source.read_text() == '{"entire":"feedback"}'
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert ProposalWorkspace.recover(output, config()) == proof


def test_public_feedback_parent_stops_tools_and_is_cleaned_on_close(tmp_path):
    workspace = setup(tmp_path)
    parent = workspace.feedback.parent
    assert parent.name.startswith(".rh-proposer-feedback-")
    parent.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        workspace.call("read_file", {"path": "feedback/full.bin"})
    assert not workspace._state["calls"]
    proof = workspace.close()
    assert proof["closed"] and proof["quiescent"] and not proof["snapshot_valid"]
    assert proof["feedback_view_removed"] and not parent.exists()


@pytest.mark.parametrize("damage", ["symlink", "extra-entry", "unknown-layout"])
def test_feedback_cleanup_refuses_foreign_paths_and_unknown_layout(tmp_path, damage):
    workspace = setup(tmp_path)
    parent = workspace.feedback.parent
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "keep.txt"
    sentinel.write_text("unrelated bytes")
    backup = parent.with_name(parent.name + "-saved")
    if damage == "symlink":
        parent.rename(backup)
        parent.symlink_to(foreign, target_is_directory=True)
    elif damage == "extra-entry":
        (parent / "unexpected.txt").write_text("preserve this too")
    else:
        workspace._state["feedback_view_layout"] = "future-layout"
        workspace._save()
    try:
        proof = workspace.close()
        assert proof["closed"] and not proof["quiescent"] and not proof["snapshot_valid"]
        assert not proof["feedback_view_removed"]
        assert sentinel.read_text() == "unrelated bytes"
        if damage == "symlink":
            assert parent.is_symlink() and (backup / "feedback/full.bin").is_file()
        else:
            assert (workspace.feedback / "full.bin").is_file()
            if damage == "extra-entry":
                assert (parent / "unexpected.txt").read_text() == "preserve this too"
    finally:
        if damage == "symlink":
            parent.unlink()
            backup.rename(parent)
        elif damage == "extra-entry":
            (parent / "unexpected.txt").unlink()
        else:
            workspace._state["feedback_view_layout"] = "private-parent-v1"
            workspace._save()
        recovered = ProposalWorkspace.recover(workspace.output, workspace.config)
        assert recovered["quiescent"] and not recovered["snapshot_valid"]
        assert not parent.exists() and sentinel.read_text() == "unrelated bytes"


def test_legacy_flat_feedback_copy_recovers_without_rewriting_source(tmp_path):
    workspace = setup(tmp_path)
    parent = workspace.feedback.parent
    staging = workspace.output / "legacy-staging"
    # macOS requires write permission on a directory moved to another parent.
    workspace.feedback.chmod(0o755)
    workspace.feedback.rename(staging)
    parent.rmdir()
    staging.rename(parent)
    parent.chmod(0o555)
    workspace._state.pop("feedback_view_layout")
    workspace._state["feedback"] = str(parent)
    workspace._save()
    proof = ProposalWorkspace.recover(workspace.output, workspace.config)
    assert proof["snapshot_valid"] and proof["quiescent"] and proof["feedback_view_removed"]
    assert not parent.exists()
    source = workspace.feedback_source / "nested/private-mode.json"
    assert source.read_text() == '{"entire":"feedback"}'
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert ProposalWorkspace.recover(workspace.output, workspace.config) == proof


@pytest.mark.parametrize("point", ["view", "parent"])
def test_feedback_removal_interruption_is_recoverable_without_replay(tmp_path, monkeypatch, point):
    workspace = setup(tmp_path)
    view, parent = workspace.feedback, workspace.feedback.parent
    original_rmtree, original_rmdir = module.shutil.rmtree, Path.rmdir

    def interrupt_view(path, *args, **kwargs):
        result = original_rmtree(path, *args, **kwargs)
        if point == "view" and path == view:
            raise OSError("Interrupted after feedback removal")
        return result

    def interrupt_parent(path, *args, **kwargs):
        result = original_rmdir(path, *args, **kwargs)
        if point == "parent" and path == parent:
            raise OSError("Interrupted after parent removal")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(module.shutil, "rmtree", interrupt_view)
        patch.setattr(Path, "rmdir", interrupt_parent)
        proof = workspace.close()
    assert proof["closed"] and not proof["quiescent"] and not proof["feedback_view_removed"]
    assert not view.exists() and parent.exists() == (point == "view")
    recovered = ProposalWorkspace.recover(workspace.output, workspace.config)
    assert recovered["closed"] and recovered["quiescent"] and not recovered["snapshot_valid"]
    assert recovered["feedback_view_removed"] and not parent.exists()
    assert not list((workspace.output / "tools").iterdir())
    assert ProposalWorkspace.recover(workspace.output, workspace.config) == recovered


def test_settled_feedback_listing_skips_content_reads_but_close_is_fresh(
    tmp_path, monkeypatch, settled_verification
):
    workspace = setup(tmp_path)
    settled_verification(workspace._verification)
    workspace.call("list_files", {"path": "feedback/", "limit": 1})
    targets = {root / "full.bin" for root in (workspace.feedback_source, workspace.feedback)}
    observed, original = [], os.open

    def opened(path, flags, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path) in targets:
            observed.append(Path(path))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    for _ in range(3):
        assert workspace.call("list_files", {"path": "feedback/", "limit": 1})["total_files"] == 2
    assert not observed
    assert workspace.close()["snapshot_valid"]
    assert set(observed) == targets


@pytest.mark.parametrize("which", ["feedback_source", "feedback"])
def test_settled_feedback_detects_same_size_restored_mtime_edits(
    tmp_path, settled_verification, which
):
    workspace = setup(tmp_path)
    path = getattr(workspace, which) / "full.bin"
    path.chmod(0o644)
    settled_verification(workspace._verification)
    workspace.call("list_files", {"path": "feedback/", "limit": 1})
    before, raw = path.stat(), path.read_bytes()
    path.write_bytes(b"x" + raw[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="Feedback changed"):
        workspace.call("list_files", {"path": "feedback/", "limit": 1})
    proof = workspace.close()
    assert not proof["snapshot_valid"] and proof["quiescent"] and proof["feedback_view_removed"]


def test_defaults_are_usable_and_impossible_protocol_bounds_rejected():
    assert config().max_files == 128
    with pytest.raises(ValueError, match="protocol"):
        config(max_workspace_bytes=1024 * 1024)
    with pytest.raises(ValueError):
        config(max_calls=True)
    with pytest.raises(ValueError):
        config(max_tool_output_bytes=2048)


def test_complete_binary_and_list_pagination_and_revocation(tmp_path):
    workspace = setup(tmp_path)
    assert workspace.feedback != workspace.feedback_source
    assert workspace.output.parent not in workspace.feedback.parents
    assert not (workspace.output / "feedback-view").exists()
    assert workspace.feedback_inventory == {
        "full.bin": digest(bytes(range(256)) * 1000),
        "nested/private-mode.json": digest('{"entire":"feedback"}'),
    }
    first = workspace.call("list_files", {"path": "feedback/", "limit": 1})
    second = workspace.call(
        "list_files", {"path": "feedback/", "offset": first["next_offset"], "limit": 1}
    )
    assert first["total_files"] == 2 and second["next_offset"] is None
    assert first["files"][0]["path"] == "feedback/full.bin"
    raw, offset = bytearray(), 0
    while offset is not None:
        page = workspace.call(
            "read_file", {"path": "feedback/full.bin", "offset": offset, "limit": 65536}
        )
        assert len(canonical_json(page).encode()) <= workspace.config.max_tool_output_bytes
        raw.extend(base64.b64decode(page["content"]))
        offset = page["next_offset"]
    assert bytes(raw) == bytes(range(256)) * 1000
    write(workspace, "workspace/candidates/a/strategy.py", "def apply(event): return event\n")
    proof = workspace.close()
    assert proof["closed"] and proof["quiescent"] and proof["snapshot_valid"]
    assert proof["feedback_sha256"] == digest(canonical_json(workspace.feedback_inventory))
    assert proof["feedback_view_removed"] and not workspace.feedback.exists()
    assert workspace.close() == proof
    assert workspace.snapshot_files(["workspace/candidates/a/strategy.py"]) == {
        "workspace/candidates/a/strategy.py": b"def apply(event): return event\n"
    }
    with pytest.raises(ValueError, match="closed"):
        workspace.call("read_file", {"path": "feedback/full.bin"})
    with pytest.raises(ValueError):
        workspace.snapshot_files(["feedback/full.bin"])
    with pytest.raises(FileExistsError):
        ProposalWorkspace(workspace.feedback_source, workspace.output, workspace.config)
    assert len(list((workspace.output / "tools").glob("*/request.json"))) == len(
        workspace._state["calls"]
    )


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "workspace/../secret",
        "feedback/../secret",
        "workspace/a/../../x",
        "workspace//x",
        "workspace/a\\b",
        "workspace/x\x00",
    ],
)
def test_virtual_paths_cannot_escape(tmp_path, path):
    workspace = setup(tmp_path)
    result = workspace.call("read_file", {"path": path})
    assert result["ok"] is False
    assert workspace.close()["snapshot_valid"]


def test_writes_are_bounded_feedback_readonly_and_errors_retained(tmp_path):
    workspace = setup(tmp_path, config(max_file_bytes=1024, max_workspace_bytes=2048, max_files=2))
    assert not workspace.call("write_file", {"path": "feedback/full.bin", "content": "overwrite"})[
        "ok"
    ]
    assert not workspace.call("write_file", {"path": "workspace/a", "content": "x" * 1025})["ok"]
    write(workspace, "workspace/a", "a" * 1024)
    write(workspace, "workspace/b", "b" * 1024)
    assert not workspace.call("write_file", {"path": "workspace/c", "content": "c"})["ok"]
    assert not workspace.call("read_file", {"path": "workspace/a", "offset": True})["ok"]
    assert not workspace.call("unknown", {})["ok"]
    assert len(list((workspace.output / "tools").glob("*/error.json"))) == 5
    assert workspace.close()["snapshot_valid"]


@pytest.mark.parametrize(
    "original, alias",
    [
        ("Strategy.py", "strategy.py"),
        ("Folder/a.py", "folder/b.py"),
        ("caf\u00e9.py", "cafe\u0301.py"),
        ("file", "file/child.py"),
        ("folder/child.py", "folder"),
    ],
)
def test_path_collisions_are_rejected_before_mutation_and_remain_repairable(
    tmp_path, original, alias
):
    workspace = setup(tmp_path)
    write(workspace, "workspace/" + original, "original")
    before = module._inventory(workspace.workspace, workspace.config)
    result = workspace.call("write_file", {"path": "workspace/" + alias, "content": "bad"})
    assert result["ok"] is False
    assert workspace._state["status"] == "ready"
    assert module._inventory(workspace.workspace, workspace.config) == before
    assert (workspace.workspace / original).read_text() == "original"
    write(workspace, "workspace/repaired.py", "valid")
    proof = workspace.close()
    assert proof["snapshot_valid"] and proof["quiescent"]
    assert workspace.snapshot_files(["workspace/repaired.py"]) == {
        "workspace/repaired.py": b"valid"
    }


@pytest.mark.parametrize("value", ["\ud800", "\udfff", "\ud800x\udfff"])
@pytest.mark.parametrize("argument", ["path", "content", "python-arguments"])
def test_surrogate_arguments_are_recorded_and_repairable(tmp_path, value, argument):
    workspace = setup(tmp_path)
    args = {"path": "workspace/code.py", "content": "original"}
    name = "write_file"
    if argument == "python-arguments":
        name, args = "run_python", {"path": "workspace/code.py", "arguments": [value]}
    else:
        args[argument] = value
    result = workspace.call(name, args)
    assert result["ok"] is False
    assert result["error_type"] == "ValueError"
    assert workspace._state["status"] == "ready"
    artifact = workspace.output / "tools/tool-000001"
    raw = (artifact / "request.json").read_bytes()
    assert json.loads(raw) == {"name": name, "arguments": args}
    assert workspace._state["calls"][0]["request_sha256"] == digest(raw)
    assert not (artifact / "execution").exists()
    write(workspace, "workspace/repaired.py", "valid")
    assert workspace.close()["snapshot_valid"]


@pytest.mark.parametrize("character", ["\u200b", "\x85", "\x7f"])
def test_nonprintable_paths_are_rejected_without_workspace_changes(tmp_path, character):
    workspace = setup(tmp_path)
    result = workspace.call(
        "write_file", {"path": f"workspace/bad{character}.py", "content": "bad"}
    )
    assert result["ok"] is False
    assert not list(workspace.workspace.iterdir())
    write(workspace, "workspace/repaired.py", "valid")
    assert workspace.close()["snapshot_valid"]


def test_empty_directory_after_failed_write_cannot_be_aliased(tmp_path, monkeypatch):
    workspace = setup(tmp_path)
    atomic_write = module.atomic_write

    def no_space(path, raw):
        if path == workspace.workspace / "Folder/a.py":
            raise OSError("Authored disk-full failure")
        return atomic_write(path, raw)

    monkeypatch.setattr(module, "atomic_write", no_space)
    assert not workspace.call("write_file", {"path": "workspace/Folder/a.py", "content": "valid"})[
        "ok"
    ]
    assert (workspace.workspace / "Folder").is_dir()
    assert not workspace.call("write_file", {"path": "workspace/folder/b.py", "content": "bad"})[
        "ok"
    ]
    assert workspace._state["status"] == "ready"
    write(workspace, "workspace/Folder/b.py", "valid")
    assert workspace.close()["snapshot_valid"]


@pytest.mark.parametrize("failure", ["missing-image", "unknown-flag", "proxy-error"])
def test_create_rejection_preserves_repairability_only_with_definitive_evidence(
    tmp_path, monkeypatch, failure
):
    import research_harness.strategies.sandbox as sandbox_module

    command_log = tmp_path / "docker-commands.jsonl"
    stub = tmp_path / "docker-stub"
    message = {
        "missing-image": f"Error response from daemon: No such image: {IMAGE}\n",
        "unknown-flag": (
            "unknown flag: --memory\n\n"
            "Usage:  docker create [OPTIONS] IMAGE [COMMAND] [ARG...]\n\n"
            "Run 'docker create --help' for more information\n"
        ),
        "proxy-error": "Error response from daemon: upstream request timed out\n",
    }[failure]
    # Authored CLI stub, not candidate code. Exercise the real subprocess boundary.
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"with pathlib.Path({str(command_log)!r}).open('a') as log:\n"
        "    log.write(json.dumps(args) + '\\n')\n"
        "if args[0] == 'info':\n"
        "    print(json.dumps({'OSType': 'linux', 'SecurityOptions': ['name=seccomp']}))\n"
        "elif args[:2] == ['image', 'inspect']:\n"
        f"    print(json.dumps([{{'Id': 'sha256:' + 'a' * 64, 'Os': 'linux', 'RepoDigests': [{IMAGE!r}], 'Config': {{}}}}]))\n"
        "elif args[0] == 'create':\n"
        f"    sys.stderr.write({message!r})\n"
        f"    sys.exit({125 if failure == 'unknown-flag' else 1})\n"
        "elif args[0] == 'inspect':\n"
        "    sys.stderr.write('Error: No such container\\n')\n"
        "    sys.exit(1)\n"
        "else:\n"
        "    sys.exit('Unexpected docker command')\n"
    )
    stub.chmod(0o755)
    monkeypatch.setattr(sandbox_module.shutil, "which", lambda _: str(stub))
    workspace = setup(tmp_path)
    write(workspace, "workspace/program.py", "raise RuntimeError('must never run')\n")
    if failure == "proxy-error":
        with pytest.raises(RuntimeError, match="uncertain"):
            workspace.call("run_python", {"path": "workspace/program.py"})
        saved = execution(workspace)
        assert saved["cleanup"] == "absent_at_check_creation_unconfirmed"
        assert "creation_rejection" not in saved
        proof = workspace.close()
        assert not proof["quiescent"] and not proof["snapshot_valid"]
        workspace._remove_feedback_view()  # The stub never creates an actual container.
    else:
        result = workspace.call("run_python", {"path": "workspace/program.py"})
        assert result["ok"] is False
        saved = execution(workspace)
        assert saved["cleanup"] == "not_created"
        assert saved["creation_acknowledged"] is False
        assert saved["creation_rejection"]["stderr"] == message
        assert workspace._state["status"] == "ready"
        write(workspace, "workspace/repaired.py", "valid")
        proof = workspace.close()
        assert proof["snapshot_valid"] and proof["quiescent"]
        attempt = workspace.output / "tools/tool-000002/execution"
        runner = sandbox_module.DockerStrategyRunner(workspace.config.sandbox, docker=stub)
        assert runner.recover(attempt)["cleanup"] == "not_created"
        assert runner.recover(attempt)["cleanup"] == "not_created"
    commands = [json.loads(line) for line in command_log.read_text().splitlines()]
    assert sum(command[0] == "create" for command in commands) == 1
    assert not any(command[0] == "start" for command in commands)
    if failure != "proxy-error":
        assert not any(command[0] in {"inspect", "rm"} for command in commands)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_feedback_refuses_links_and_special_files(tmp_path, kind):
    feedback = tmp_path / "feedback"
    feedback.mkdir()
    target = tmp_path / "private"
    target.write_text("HOST_CANARY")
    if kind == "symlink":
        (feedback / "link").symlink_to(target)
    elif kind == "hardlink":
        os.link(target, feedback / "link")
    else:
        os.mkfifo(feedback / "fifo")
    with pytest.raises(ValueError):
        ProposalWorkspace(feedback, tmp_path / "access", config())
    assert not (tmp_path / "access").exists()


@pytest.mark.parametrize("tree", ["feedback", "feedback_source", "workspace"])
def test_external_mutation_prevents_close_snapshot(tmp_path, tree):
    workspace = setup(tmp_path)
    root = getattr(workspace, tree)
    if tree == "feedback":
        root.chmod(0o755)
    (root / "external").write_text("outside tool")
    proof = workspace.close()
    assert proof["closed"] and proof["quiescent"] and not proof["snapshot_valid"]
    assert workspace.close() == proof
    with pytest.raises(ValueError):
        workspace.snapshot_files([])


def test_missing_state_and_active_owner_cannot_be_recreated_or_recovered(tmp_path):
    workspace = setup(tmp_path)
    with FileLock(str(workspace.output / "owner.lock")):
        with pytest.raises(Timeout):
            ProposalWorkspace.recover(workspace.output, workspace.config)
    (workspace.output / "workspace.json").unlink()
    with pytest.raises(FileNotFoundError):
        ProposalWorkspace.recover(workspace.output, workspace.config)
    with pytest.raises(FileExistsError):
        ProposalWorkspace(workspace.feedback_source, workspace.output, workspace.config)
    workspace._remove_feedback_view()  # Simulated metadata loss; no execution was started.


def test_crashed_tool_never_replays_and_prevents_candidate_success(tmp_path, monkeypatch):
    workspace = setup(tmp_path)

    def interrupt(*args):
        raise KeyboardInterrupt("host interrupted")

    monkeypatch.setattr(workspace, "_operate", interrupt)
    with pytest.raises(KeyboardInterrupt):
        workspace.call("run_python", {"path": "workspace/example.py"})
    monkeypatch.setattr(module._WorkspaceRunner, "execute", lambda *args: pytest.fail("replayed"))
    result = ProposalWorkspace.recover(workspace.output, workspace.config)
    assert result["closed"] and result["quiescent"] and not result["snapshot_valid"]
    assert result["replayed"] is False
    assert ProposalWorkspace.recover(workspace.output, workspace.config) == result
    with pytest.raises(ValueError):
        workspace.call("write_file", {"path": "workspace/x", "content": "bad"})


def test_unknown_create_acknowledgement_cannot_claim_quiescence(tmp_path, monkeypatch):
    workspace = setup(tmp_path)
    artifact = workspace.output / "tools/tool-000001/execution"
    artifact.mkdir(parents=True)
    report = {
        "status": "creating",
        "cleanup": "not_created",
        "creation_acknowledged": False,
        "configuration": workspace.config.sandbox.model_dump(mode="json"),
        "container_name": "rh-strategy-owned",
        "execution_id": "owned",
    }
    write_json(artifact / "execution.json", report)
    workspace._state.update(status="pending", calls=[{"id": "tool-000001", "status": "pending"}])
    workspace._save()
    monkeypatch.setattr(module._WorkspaceRunner, "_remove_owned", lambda *args: "absent")
    proof = ProposalWorkspace.recover(workspace.output, workspace.config)
    assert proof["closed"] and not proof["quiescent"] and not proof["snapshot_valid"]
    assert (
        json.loads((artifact / "execution.json").read_bytes())["cleanup"]
        == "absent_at_check_creation_unconfirmed"
    )
    workspace._remove_feedback_view()  # Synthetic unknown-create test has no real container.


def actual_workspace(tmp_path, **sandbox):
    image = os.environ.get("RH_TEST_STRATEGY_IMAGE")
    if not image:
        pytest.skip("Set RH_TEST_STRATEGY_IMAGE to the local pinned Python image")
    return setup(tmp_path, WorkspaceConfig(sandbox=SandboxConfig(image=image, **sandbox)))


def execution(workspace):
    files = sorted((workspace.output / "tools").glob("*/execution/execution.json"))
    return json.loads(files[-1].read_bytes())


def test_actual_docker_full_feedback_edit_execute_and_resource_cleanup(tmp_path, monkeypatch):
    workspace = actual_workspace(tmp_path)
    parent = workspace.feedback.parent
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    host = tmp_path / "heldout.txt"
    host.write_text("HELDOUT_PRIVATE_CANARY")
    monkeypatch.setenv("OPENAI_API_KEY", "HOST_CREDENTIAL_CANARY")
    code = f"""import pathlib, os, socket, hashlib, json, subprocess
root = pathlib.Path('/workspace')
assert pathlib.Path('/feedback/full.bin').read_bytes() == bytes(range(256)) * 1000
assert pathlib.Path('/feedback/nested/private-mode.json').read_text() == '{{"entire":"feedback"}}'
assert 'OPENAI_API_KEY' not in os.environ
assert not pathlib.Path({str(host)!r}).exists()
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/input/../../Users').exists()
for path in ['/feedback/full.bin', '/seed/task.py', '/etc/host-write']:
    try:
        pathlib.Path(path).write_text('changed')
    except OSError:
        pass
    else:
        raise AssertionError('read-only restriction missing')
try:
    socket.create_connection(('1.1.1.1', 53), timeout=0.2)
except OSError:
    pass
else:
    raise AssertionError('network enabled')
(root / 'result.bin').write_bytes(b'edited\\x00\\xff')
print('complete stdout')
os.write(2, b'complete stderr')
# A background descendant must not survive completion.
subprocess.Popen(['/usr/local/bin/python3', '-c', 'import time; time.sleep(30)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""
    write(workspace, "workspace/task.py", code)
    result = workspace.call("run_python", {"path": "workspace/task.py"})
    assert result["ok"], list((workspace.output / "tools").glob("*/error.json"))
    assert result["edits_persisted"] and result["cleanup"] == "removed"
    assert base64.b64decode(result["stdout"]) == b"complete stdout\n"
    assert base64.b64decode(result["stderr"]) == b"complete stderr"
    assert (workspace.workspace / "result.bin").read_bytes() == b"edited\0\xff"
    saved = execution(workspace)
    assert saved["container"]["HostConfig"]["NetworkMode"] == "none"
    assert all(not row["RW"] for row in saved["container"]["Mounts"])
    assert "/workspace" in saved["container"]["HostConfig"]["Tmpfs"]
    assert saved["status"] == "completed" and saved["cleanup"] == "removed"
    proof = workspace.close()
    assert proof["snapshot_valid"] and proof["quiescent"]
    assert not parent.exists()
    assert host.read_text() == "HELDOUT_PRIVATE_CANARY"
    assert (
        workspace.snapshot_files(["workspace/result.bin"])["workspace/result.bin"]
        == b"edited\0\xff"
    )


@pytest.mark.parametrize(
    "code",
    [
        "from pathlib import Path\nPath('/workspace/new').write_text('not committed')\nraise RuntimeError('failed')\n",
        "from pathlib import Path\nPath('/workspace/link').symlink_to('/feedback/full.bin')\n",
        "import os\nos.link('/workspace/task.py', '/workspace/hardlink')\n",
        "import os\nos.mkfifo('/workspace/fifo')\n",
        "from pathlib import Path\nPath('/workspace/oversize').write_bytes(b'x' * 300000)\n",
        "import os\nwhile True: os.write(1, b'x' * 65536)\n",
    ],
)
def test_actual_failures_never_publish_edits(tmp_path, code):
    workspace = actual_workspace(tmp_path)
    write(workspace, "workspace/task.py", code)
    result = workspace.call("run_python", {"path": "workspace/task.py"})
    assert not result["ok"]
    assert list(workspace.workspace.iterdir()) == [workspace.workspace / "task.py"]
    assert execution(workspace)["cleanup"] == "removed"
    assert workspace.close()["snapshot_valid"]
    assert not workspace.feedback.parent.exists()


def test_actual_timeout_is_cleaned_and_not_replayed(tmp_path):
    workspace = actual_workspace(tmp_path, timeout_seconds=2)
    write(
        workspace,
        "workspace/task.py",
        "import time\nprint('before timeout', flush=True)\ntime.sleep(60)\n",
    )
    result = workspace.call("run_python", {"path": "workspace/task.py"})
    assert not result["ok"]
    assert execution(workspace)["cleanup"] == "removed"
    before = len(list((workspace.output / "tools").glob("*/execution/execution.json")))
    proof = ProposalWorkspace.recover(workspace.output, workspace.config)
    assert proof["closed"] and proof["quiescent"]
    assert len(list((workspace.output / "tools").glob("*/execution/execution.json"))) == before


def test_readable_feedback_is_mounted_without_recursive_copy(tmp_path):
    feedback = tmp_path / "feedback"
    feedback.mkdir(mode=0o755)
    (feedback / "full.txt").write_text("complete bytes")
    (feedback / "full.txt").chmod(0o644)
    workspace = ProposalWorkspace(feedback, tmp_path / "access", config())
    assert workspace.feedback == workspace.feedback_source
    assert not workspace._state["feedback_view_owned"]
    assert workspace.close()["snapshot_valid"]
    assert feedback.is_dir() and (feedback / "full.txt").read_text() == "complete bytes"


def test_candidate_source_is_never_host_imported(tmp_path, monkeypatch):
    workspace = setup(tmp_path)
    sentinel = tmp_path / "host-executed"
    write(
        workspace,
        "workspace/task.py",
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n",
    )

    def unavailable(*args):
        raise ValueError("Docker unavailable")

    monkeypatch.setattr(module, "_WorkspaceRunner", unavailable)
    assert not workspace.call("run_python", {"path": "workspace/task.py"})["ok"]
    assert not sentinel.exists()
    assert workspace.close()["snapshot_valid"]


def test_report_loss_preserves_unknown_resource_state(tmp_path):
    workspace = setup(tmp_path)
    workspace._state.update(
        status="pending",
        calls=[{"id": "tool-000001", "status": "pending", "execution_expected": True}],
    )
    workspace._save()
    proof = ProposalWorkspace.recover(workspace.output, workspace.config)
    assert proof["closed"] and not proof["quiescent"]
    assert not proof["snapshot_valid"] and not proof["feedback_view_removed"]
    # No real container exists in this synthetic loss test; the proof remains unknown.
    workspace._remove_feedback_view()


def test_failed_publication_cannot_be_admitted_as_a_candidate(tmp_path, monkeypatch):
    workspace = setup(tmp_path)
    write(workspace, "workspace/task.py", "print('old')")
    original = module.write_json

    def interrupt_commit(path, value):
        if path.name == "publication.json" and value["status"] == "completed":
            raise OSError("disk full after directory swap")
        return original(path, value)

    monkeypatch.setattr(module, "write_json", interrupt_commit)

    def publish(*args):
        return workspace._publish({"task.py": base64.b64encode(b"print('new')").decode()}, args[-1])

    monkeypatch.setattr(workspace, "_operate", publish)
    with pytest.raises(RuntimeError, match="uncertain"):
        workspace.call("run_python", {"path": "workspace/task.py"})
    proof = workspace.close()
    assert proof["closed"] and proof["quiescent"] and not proof["snapshot_valid"]
    with pytest.raises(ValueError):
        workspace.snapshot_files(["workspace/task.py"])


def test_actual_hard_exit_recovery_terminates_owned_container_without_replay(tmp_path):
    import subprocess
    import sys
    import time

    image = os.environ.get("RH_TEST_STRATEGY_IMAGE")
    if not image:
        pytest.skip("Set RH_TEST_STRATEGY_IMAGE to the local pinned Python image")
    feedback = tmp_path / "feedback"
    feedback.mkdir()
    (feedback / "input.txt").write_text("full feedback")
    (feedback / "input.txt").chmod(0o600)
    settings = WorkspaceConfig(sandbox=SandboxConfig(image=image, timeout_seconds=30))
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(settings.model_dump_json())
    output = tmp_path / "interrupted"
    host_code = """
import sys
from pathlib import Path
from research_harness.optimization.workspace import ProposalWorkspace, WorkspaceConfig
settings = WorkspaceConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
workspace = ProposalWorkspace(Path(sys.argv[2]), Path(sys.argv[3]), settings)
workspace.call('write_file', {'path':'workspace/task.py', 'content':"import time\\nprint('started', flush=True)\\ntime.sleep(60)\\n"})
workspace.call('run_python', {'path':'workspace/task.py'})
"""
    report_path = output / "tools/tool-000002/execution/execution.json"
    with (tmp_path / "host.log").open("wb") as log:
        child = subprocess.Popen(
            [sys.executable, "-c", host_code, str(settings_path), str(feedback), str(output)],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while (
                not report_path.exists()
                or json.loads(report_path.read_bytes())["status"] != "running"
            ):
                assert child.poll() is None, (tmp_path / "host.log").read_text()
                assert time.monotonic() < deadline, "workspace did not enter Docker running state"
                time.sleep(0.02)
            child.kill()
            child.wait(timeout=5)
            report_before = json.loads(report_path.read_bytes())
            assert report_before["cleanup"] == "not_created"
            assert report_before["creation_acknowledged"]
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            proof = ProposalWorkspace.recover(output, settings)
    assert proof["closed"] and proof["quiescent"] and not proof["snapshot_valid"]
    assert proof["replayed"] is False
    state = json.loads((output / "workspace.json").read_bytes())
    assert state["feedback_view_owned"] and proof["feedback_view_removed"]
    assert not Path(state["feedback"]).parent.exists()
    saved = json.loads(report_path.read_bytes())
    assert saved["status"] == "failed" and saved["cleanup"] == "removed"
    assert saved["recovery"]["replayed"] is False
    assert len(list((output / "tools").glob("*/execution/execution.json"))) == 1
    absent = subprocess.run(
        ["docker", "inspect", report_before["container_id"]], capture_output=True
    )
    assert absent.returncode != 0 and b"no such" in absent.stderr.lower()


def test_cleanup_never_removes_a_foreign_container(tmp_path, monkeypatch):
    import subprocess

    workspace = setup(tmp_path)
    artifact = workspace.output / "tools/tool-000001/execution"
    artifact.mkdir(parents=True)
    write_json(
        artifact / "execution.json",
        {
            "status": "running",
            "cleanup": "not_created",
            "creation_acknowledged": True,
            "configuration": workspace.config.sandbox.model_dump(mode="json"),
            "container_name": "rh-strategy-recorded",
            "execution_id": "recorded",
        },
    )
    workspace._state.update(
        status="pending",
        calls=[{"id": "tool-000001", "status": "pending", "execution_expected": True}],
    )
    workspace._save()

    def command(self, args, **kwargs):
        assert args[0] == "inspect", "Foreign container must not receive rm/stop"
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                [
                    {
                        "Id": "foreign-container",
                        "Config": {"Labels": {module.DockerStrategyRunner.__module__: "foreign"}},
                    }
                ]
            ).encode(),
            b"",
        )

    monkeypatch.setattr(module._WorkspaceRunner, "_command", command)
    proof = workspace.close()
    assert proof["closed"] and not proof["quiescent"] and not proof["snapshot_valid"]
    error = json.loads((workspace.output / "cleanup-errors.json").read_bytes())
    assert "ownership" in error[0]["error"]
    workspace._remove_feedback_view()  # No real container exists in this synthetic ownership test.


def test_uncertain_resources_escape_tool_call_and_stop_model_repair_loop(tmp_path, monkeypatch):
    workspace = setup(tmp_path)

    def uncertain(name, args, artifact):
        workspace._state["calls"][-1]["execution_expected"] = True
        raise RuntimeError("Execution report could not be recovered")

    monkeypatch.setattr(workspace, "_operate", uncertain)
    with pytest.raises(RuntimeError, match="uncertain"):
        workspace.call("run_python", {"path": "workspace/task.py"})
    state = json.loads((workspace.output / "workspace.json").read_bytes())
    assert state["status"] == "uncertain"
    assert (workspace.output / "tools/tool-000001/result.json").exists()
    with pytest.raises(ValueError, match="requires recovery"):
        workspace.call("list_files", {"path": "feedback/"})
    workspace._remove_feedback_view()  # Synthetic unknown-resource test starts no container.
