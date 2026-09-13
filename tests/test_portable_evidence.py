from __future__ import annotations

import gzip
import hashlib
import json
import os
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from defusedxml.common import DefusedXmlException

from research_harness.evaluation import portable_evidence as portable


@pytest.fixture
def bindings():
    return {
        "REPOSITORY": {"kind": "path", "value": "/Users/author/research"},
        "PYTHON": {"kind": "path", "value": "/Users/author/research/.venv/bin/python"},
        "RUN": {"kind": "path", "value": "/private/tmp/captured-run"},
        "HOST": {"kind": "literal", "value": "operator-machine.example"},
    }


@pytest.fixture
def source(tmp_path, bindings):
    root = tmp_path / "source"
    (root / "agent").mkdir(parents=True)
    tool = {
        "command": bindings["PYTHON"]["value"],
        "args": [
            bindings["REPOSITORY"]["value"] + "/fixture.py",
            "--case",
            bindings["RUN"]["value"] + "/case.json",
        ],
    }
    (root / "agent/research.yaml").write_text(json.dumps(tool, indent=2) + "\n")
    (root / "metadata.json").write_text(
        json.dumps({"command": tool["command"], "host": bindings["HOST"]["value"]})
    )
    (root / "events.jsonl").write_text(
        '{"file":"\\u002fUsers/author/research/code.py"}\n'
        "partial /private/tmp/captured-run/artifact\n"
    )
    (root / "pytest.xml").write_text(
        '<testsuites><testsuite hostname="different-old-host.example" tests="1" failures="0">'
        '<testcase name="fixture" file="/Users/author/research/tests/test.py" />'
        "</testsuite></testsuites>"
    )
    (root / "feed.xml").write_text(
        '<feed hostname="scientific-source.example"><value>42</value></feed>'
    )
    (root / "unchanged.json").write_bytes(b'{ "score" : 1.0, "fixture_only" : true }\n')
    (root / "bytes.bin").write_bytes(bytes(range(256)))
    (root / "strategy.py").write_text("raise RuntimeError('never execute candidate code')\n")
    (root / "strategy.py").chmod(0o755)
    return root


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture
def review(tmp_path, source, bindings):
    output = tmp_path / "review"
    portable.export_review(source, output, bindings)
    return output


def test_review_removes_bound_paths_and_junit_hosts_preserving_originals(
    source, tmp_path, bindings
):
    before = hashes(source)
    output = tmp_path / "review"
    result = portable.export_review(source, output, bindings)
    assert result["files"] == 8 and result["changed_files"] == 4
    assert hashes(source) == before
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(not p.stat().st_mode & 0o111 for p in (output / "files").rglob("*") if p.is_file())
    manifest = json.loads((output / portable.MANIFEST).read_bytes())
    assert manifest["role"] == "derived_review_only"
    assert manifest["original_evidence_required_for_runtime_audit"] is True
    for path in output.rglob("*"):
        if path.is_file():
            assert not portable._contains(path.read_bytes(), bindings)
    for name in ("unchanged.json", "feed.xml", "bytes.bin", "strategy.py"):
        assert (output / "files" / name).read_bytes() == (source / name).read_bytes()
    tool = json.loads((output / "files/agent/research.yaml").read_bytes())
    assert tool["command"] == "${RH_REVIEW_PYTHON}"
    assert tool["args"][0] == "${RH_REVIEW_REPOSITORY}/fixture.py"
    junit = ET.parse(output / "files/pytest.xml").getroot()
    assert junit.find("testsuite").get("hostname") is None
    assert junit.find("testsuite").get("tests") == "1"
    assert "\\u002fUsers" not in (output / "files/events.jsonl").read_text()
    assert not portable.verify_review(output)["source_and_transforms_verified"]
    assert portable.verify_review(output, source=source, bindings=bindings)[
        "source_and_transforms_verified"
    ]


def test_paths_use_longest_match_and_do_not_rewrite_neighbor_prefixes(bindings):
    value = bindings["PYTHON"]["value"]
    assert portable._replace(value, bindings) == "${RH_REVIEW_PYTHON}"
    neighbor = bindings["REPOSITORY"]["value"] + "-other/file"
    assert portable._replace(neighbor, bindings) == neighbor
    assert not portable._contains(neighbor.encode(), bindings)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16le", "utf-16be"])
def test_binary_identity_fails_without_a_completion_manifest(source, bindings, tmp_path, encoding):
    path = source / "private.bin"
    original = (
        b"\0\xff" + bindings["REPOSITORY"]["value"].encode(encoding) + "/file".encode(encoding)
    )
    path.write_bytes(original)
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="unsupported binary"):
        portable.export_review(source, output, bindings)
    assert not (output / portable.MANIFEST).exists()
    assert path.read_bytes() == original


def test_compressed_identity_cannot_hide_in_a_nested_archive(source, bindings, tmp_path):
    (source / "trace.txt").write_bytes(
        gzip.compress(bindings["REPOSITORY"]["value"].encode(), mtime=0)
    )
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="Nested archives"):
        portable.export_review(source, output, bindings)
    assert not (output / portable.MANIFEST).exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory-link", "filename-identity"])
def test_unsafe_input_trees_are_rejected(source, bindings, tmp_path, kind):
    if kind == "symlink":
        (source / "linked").symlink_to(source / "bytes.bin")
    elif kind == "hardlink":
        os.link(source / "bytes.bin", source / "linked")
    elif kind == "directory-link":
        (source / "linked").symlink_to(source / "agent", target_is_directory=True)
    else:
        (source / (bindings["HOST"]["value"] + ".log")).write_text("fixture")
    output = tmp_path / "failed"
    with pytest.raises(ValueError):
        portable.export_review(source, output, bindings)
    assert not output.exists()


@pytest.mark.parametrize(
    "change", ["bytes", "extra", "missing", "executable", "method", "role", "source-hash"]
)
def test_changed_review_or_derivation_metadata_fails(review, change):
    path = review / "files/metadata.json"
    manifest_path = review / portable.MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    if change == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "extra":
        (review / "extra").write_text("unexpected")
    elif change == "missing":
        path.unlink()
    elif change == "executable":
        path.chmod(0o755)
    else:
        if change == "method":
            manifest["files"]["metadata.json"]["method"] = "unchanged"
        elif change == "role":
            manifest["role"] = "original_runtime_evidence"
        else:
            manifest["files"]["metadata.json"]["source_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        portable.verify_review(review)


def test_source_verification_recomputes_transformations_and_original_hashes(
    review, source, bindings
):
    wrong = {key: dict(value) for key, value in bindings.items()}
    wrong["PYTHON"]["value"] = "/some/other/python"
    with pytest.raises(ValueError, match="transformation differs"):
        portable.verify_review(review, source=source, bindings=wrong)
    (source / "metadata.json").write_text('{"changed": true}')
    with pytest.raises(ValueError, match="Original evidence differs"):
        portable.verify_review(review, source=source, bindings=bindings)
    assert portable.verify_review(review)["source_and_transforms_verified"] is False


def test_json_template_renders_foreign_paths_without_executing(review, bindings, tmp_path):
    foreign = {key: dict(value) for key, value in bindings.items()}
    foreign["REPOSITORY"]["value"] = '/work/another user/repo "quoted"'
    foreign["PYTHON"]["value"] = r"C:\Users\reviewer\.venv\Scripts\python.exe"
    foreign["RUN"]["value"] = "/work/new run"
    foreign["HOST"]["value"] = "new-machine.example"
    before = hashes(review)
    output = tmp_path / "rendered/research.json"
    assert portable.render_file(review, "agent/research.yaml", output, foreign)["executed"] is False
    tool = json.loads(output.read_bytes())
    assert tool["command"] == foreign["PYTHON"]["value"]
    assert tool["args"][0] == foreign["REPOSITORY"]["value"] + "/fixture.py"
    assert tool["args"][2] == foreign["RUN"]["value"] + "/case.json"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert hashes(review) == before
    with pytest.raises(FileExistsError):
        portable.render_file(review, "agent/research.yaml", output, foreign)
    with pytest.raises(ValueError, match="outside"):
        portable.render_file(review, "agent/research.yaml", review / "new.json", foreign)


def test_xml_template_renders_escaped_path_attributes(review, bindings, tmp_path):
    foreign = {key: dict(value) for key, value in bindings.items()}
    foreign["REPOSITORY"]["value"] = '/work/repo & "quotes"'
    output = tmp_path / "rendered.xml"
    portable.render_file(review, "pytest.xml", output, foreign)
    root = ET.parse(output).getroot()
    assert (
        root.find("testsuite/testcase").get("file")
        == foreign["REPOSITORY"]["value"] + "/tests/test.py"
    )
    assert root.find("testsuite").get("hostname") is None


@pytest.mark.parametrize(
    "case", ["empty", "root", "relative", "duplicate", "reserved", "escaped-reserved"]
)
def test_invalid_or_ambiguous_private_bindings_fail(source, bindings, tmp_path, case):
    if case == "empty":
        bindings = {}
    elif case == "root":
        bindings["RUN"]["value"] = "/"
    elif case == "relative":
        bindings["RUN"]["value"] = "../run"
    elif case == "duplicate":
        bindings["RUN"] = dict(bindings["REPOSITORY"])
    else:
        value = '"${RH_REVIEW_RUN}"'
        if case == "escaped-reserved":
            value = value.replace("$", r"\u0024")
        (source / "reserved.json").write_text(value)
    output = tmp_path / "failed"
    with pytest.raises(ValueError):
        portable.export_review(source, output, bindings)
    assert not (output / portable.MANIFEST).exists()


def test_limits_and_existing_outputs_never_truncate_or_overwrite(
    source, bindings, tmp_path, monkeypatch
):
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep").write_text("original")
    with pytest.raises(FileExistsError):
        portable.export_review(source, output, bindings)
    assert (output / "keep").read_text() == "original"
    with pytest.raises(ValueError, match="outside"):
        portable.export_review(source, source / "nested", bindings)
    monkeypatch.setattr(portable, "MAX_FILE_BYTES", 2)
    with pytest.raises(ValueError, match="byte limit"):
        portable.export_review(source, tmp_path / "too-large", bindings)
    assert not (tmp_path / "too-large" / portable.MANIFEST).exists()


def test_source_change_during_export_keeps_no_completion_manifest(
    source, bindings, tmp_path, monkeypatch
):
    original = portable._transform

    def changing(raw, name, values):
        result = original(raw, name, values)
        if name == "metadata.json":
            (source / "metadata.json").write_bytes(raw + b" ")
        return result

    monkeypatch.setattr(portable, "_transform", changing)
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="changed during"):
        portable.export_review(source, output, bindings)
    assert not (output / portable.MANIFEST).exists()


def test_cli_export_and_separate_source_verification(source, bindings, tmp_path):
    private = tmp_path / "private-bindings.json"
    private.write_text(json.dumps(bindings))
    output = tmp_path / "review"
    command = [sys.executable, "-m", "research_harness.evaluation.portable_evidence"]
    result = subprocess.run(
        [*command, "export", str(source), str(output), "--bindings", str(private)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["changed_files"] == 4
    result = subprocess.run(
        [*command, "verify", str(output), "--source", str(source), "--bindings", str(private)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["source_and_transforms_verified"] is True


def test_xml_entities_cannot_read_host_files_during_export(source, bindings, tmp_path):
    canary = tmp_path / "private-canary"
    canary.write_text("private data must not be read into the export")
    (source / "pytest.xml").write_text(
        f'<!DOCTYPE testsuite [<!ENTITY secret SYSTEM "{canary.as_uri()}">]>'
        "<testsuite><system-out>&secret;</system-out></testsuite>"
    )
    output = tmp_path / "failed"
    with pytest.raises(DefusedXmlException):
        portable.export_review(source, output, bindings)
    assert not (output / portable.MANIFEST).exists()
    assert all(canary.read_bytes() not in p.read_bytes() for p in output.rglob("*") if p.is_file())


def test_source_verification_requires_both_originals_and_bindings(review, source, bindings):
    with pytest.raises(ValueError, match="both source and private bindings"):
        portable.verify_review(review, source=source)
    with pytest.raises(ValueError, match="both source and private bindings"):
        portable.verify_review(review, bindings=bindings)


def test_render_rejects_path_escape_before_writing(review, bindings, tmp_path):
    output = tmp_path / "rendered"
    with pytest.raises(ValueError, match="Unsafe evidence member"):
        portable.render_file(review, "../outside", output, bindings)
    assert not output.exists()
