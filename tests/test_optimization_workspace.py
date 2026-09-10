from __future__ import annotations

import base64
import json
import os

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
    host = tmp_path / "heldout.txt"
    host.write_text("HELDOUT_PRIVATE_CANARY")
    monkeypatch.setenv("OPENAI_API_KEY", "HOST_CREDENTIAL_CANARY")
    code = f"""import pathlib, os, socket, hashlib, json, subprocess
root = pathlib.Path('/workspace')
assert len(pathlib.Path('/feedback/full.bin').read_bytes()) == 256000
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
