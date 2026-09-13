"""Derived review copies with private bindings; original runtime evidence is untouched."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath, PureWindowsPath

from defusedxml.ElementTree import fromstring

from research_harness.evaluation import evidence_assets as assets

KIND = "research-harness-portable-review-v1"
MANIFEST = "portable-review.json"
MAX_FILE_BYTES = 64 * 1024**2
TOKEN_PREFIX = "RH_REVIEW_"
METHODS = {
    "unchanged",
    "text-placeholders",
    "json-placeholders",
    "jsonl-placeholders",
    "junit-hostname-and-placeholders",
    "xml-placeholders",
}
PATH_END = "/\\ \t\r\n\"'<>()[]{},:;"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, limit=None):
    limit = MAX_FILE_BYTES if limit is None else limit
    with assets._open(path) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Complete review file exceeds the byte limit")
    return raw


def _bindings(value):
    if not isinstance(value, dict) or not value or len(value) > 64:
        raise ValueError("Provide 1–64 named private bindings")
    result = {}
    for name, record in value.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,47}", name):
            raise ValueError("Invalid review placeholder name")
        if not isinstance(record, dict) or set(record) != {"kind", "value"}:
            raise ValueError("Each binding needs kind and value")
        kind, text = record["kind"], record["value"]
        if (
            kind not in {"path", "literal"}
            or not isinstance(text, str)
            or not 4 <= len(text) <= 4096
            or not text.isprintable()
            or TOKEN_PREFIX in text
        ):
            raise ValueError("Invalid private binding")
        if kind == "path":
            path = (
                PureWindowsPath(text)
                if PureWindowsPath(text).is_absolute()
                else PurePosixPath(text)
            )
            if not path.is_absolute() or len(path.parts) < 3 or ".." in path.parts:
                raise ValueError("Path bindings must identify an absolute directory or file")
            text = text.rstrip("/\\")
        if any(prior["value"] == text for prior in result.values()):
            raise ValueError("Duplicate binding values are ambiguous")
        result[name] = {"kind": kind, "value": text}
    return result


def _token(name):
    return "${" + TOKEN_PREFIX + name + "}"


def _replace(text, bindings):
    if any(_token(name) in text for name in bindings):
        raise ValueError("Source contains a reserved review placeholder")
    # One pass prevents a replacement from being interpreted as another binding.
    alternatives, names = [], []
    for name, record in sorted(bindings.items(), key=lambda item: -len(item[1]["value"])):
        pattern = re.escape(record["value"])
        if record["kind"] == "path":
            pattern += r"(?=$|[/\\\s\"'<>()\[\]{},:;])"
        alternatives.append("(" + pattern + ")")
        names.append(name)
    expression = re.compile("|".join(alternatives))
    return expression.sub(lambda match: _token(names[match.lastindex - 1]), text)


def _strings(value, replace):
    if isinstance(value, str):
        return replace(value)
    if isinstance(value, list):
        return [_strings(item, replace) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            replacement = replace(key)
            if replacement in result:
                raise ValueError("Redaction would merge distinct JSON keys")
            result[replacement] = _strings(item, replace)
        return result
    return value


def _contains(raw, bindings):
    for record in bindings.values():
        value = record["value"]
        variants = [value, value.replace("/", r"\/"), json.dumps(value)[1:-1]]
        for item in variants:
            for encoding in ("utf-8", "utf-16le", "utf-16be"):
                needle, start = item.encode(encoding), 0
                while (index := raw.find(needle, start)) >= 0:
                    tail = raw[index + len(needle) :]
                    if (
                        record["kind"] == "literal"
                        or not tail
                        or any(tail.startswith(end.encode(encoding)) for end in PATH_END)
                    ):
                        return True
                    start = index + len(needle)
    return False


def _transform(raw, name, bindings):
    for key in bindings:
        if _token(key).encode() in raw:
            raise ValueError("Source contains a reserved review placeholder")
    try:
        text = raw.decode("utf-8")
        if "\0" in text:
            raise UnicodeError("Binary content")
    except UnicodeError:
        if _contains(raw, bindings):
            raise ValueError("Configured identity occurs in unsupported binary evidence") from None
        return raw, "unchanged"
    suffix = Path(name).suffix.lower()
    if suffix in {".json", ".yaml", ".yml"}:
        try:
            original = assets._json(raw)
        except (ValueError, UnicodeError):
            # Partial captures and non-JSON YAML retain their original syntax.
            transformed = _replace(text, bindings).encode()
            method = "text-placeholders"
        else:
            updated = _strings(original, lambda text: _replace(text, bindings))
            transformed = raw if updated == original else assets._encoded(updated)
            method = "json-placeholders"
    elif suffix == ".jsonl":
        lines = []
        for line in text.splitlines(keepends=True):
            try:
                original = assets._json(line.encode())
            except ValueError:
                lines.append(_replace(line, bindings))
            else:
                updated = _strings(original, lambda text: _replace(text, bindings))
                lines.append(
                    line if updated == original else json.dumps(updated, ensure_ascii=True) + "\n"
                )
        transformed, method = "".join(lines).encode(), "jsonl-placeholders"
    elif suffix == ".xml":
        # Parse safely so JUnit hostnames are removed even when not named in bindings.
        root = fromstring(raw)
        junit = root.tag.split("}")[-1] in {"testsuite", "testsuites"}
        changed = False
        for item in root.iter():
            for key, value in list(item.attrib.items()):
                if junit and key.split("}")[-1] == "hostname":
                    del item.attrib[key]
                    changed = True
                else:
                    updated = _replace(value, bindings)
                    changed |= updated != value
                    item.attrib[key] = updated
            for key in ("text", "tail"):
                value = getattr(item, key)
                if value:
                    updated = _replace(value, bindings)
                    changed |= updated != value
                    setattr(item, key, updated)
        transformed = ET.tostring(root, encoding="utf-8", xml_declaration=True) if changed else raw
        method = "junit-hostname-and-placeholders" if junit else "xml-placeholders"
    else:
        transformed, method = _replace(text, bindings).encode(), "text-placeholders"
    if _contains(transformed, bindings):
        raise ValueError("Configured identity remains after transformation")
    return transformed, method if transformed != raw else "unchanged"


def _source_inventory(files):
    return _sha(
        assets._encoded(
            {
                name: {"sha256": row["source_sha256"], "bytes": row["source_bytes"]}
                for name, row in files.items()
            }
        )
    )


def export_review(source: Path, output: Path, bindings: dict):
    bindings = _bindings(bindings)
    source, output = source.absolute(), output.absolute()
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError("Review output must be outside the original evidence")
    before = assets._tree(source)
    if any(_contains(name.encode(), bindings) for name in before):
        raise ValueError("Source filenames contain configured identity")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = {}
    for name in before:
        path = source / name
        if assets._compressed(path):
            raise ValueError("Nested archives must be decoded and reviewed before export")
        raw = _read(path)
        transformed, method = _transform(raw, name, bindings)
        target = output / "files" / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(transformed)
        # Inspection copies are deliberately not executable.
        target.chmod(0o600)
        files[name] = {
            "source_sha256": _sha(raw),
            "source_bytes": len(raw),
            "sha256": _sha(transformed),
            "bytes": len(transformed),
            "method": method,
        }
    if assets._tree(source) != before:
        raise ValueError("Original evidence changed during review export")
    manifest = {
        "kind": KIND,
        "role": "derived_review_only",
        "original_evidence_required_for_runtime_audit": True,
        "source_inventory_sha256": _source_inventory(files),
        "placeholders": {name: {"kind": value["kind"]} for name, value in sorted(bindings.items())},
        "files": files,
    }
    pending = output / "portable-review.pending.json"
    pending.write_bytes(assets._encoded(manifest))
    _inspect(output, manifest, source=source, bindings=bindings, pending=True)
    pending.rename(output / MANIFEST)
    return {
        "files": len(files),
        "changed_files": sum(row["method"] != "unchanged" for row in files.values()),
        "source_inventory_sha256": manifest["source_inventory_sha256"],
        "role": manifest["role"],
    }


def _inspect(output, manifest, *, source=None, bindings=None, pending=False):
    if (source is None) != (bindings is None):
        raise ValueError("Original evidence verification requires both source and private bindings")
    if (
        not isinstance(manifest, dict)
        or manifest.get("kind") != KIND
        or manifest.get("role") != "derived_review_only"
        or manifest.get("original_evidence_required_for_runtime_audit") is not True
        or not isinstance(manifest.get("files"), dict)
        or not manifest["files"]
        or not isinstance(manifest.get("placeholders"), dict)
    ):
        raise ValueError("Invalid portable review manifest")
    if not 1 <= len(manifest["placeholders"]) <= 64 or any(
        not re.fullmatch(r"[A-Z][A-Z0-9_]{0,47}", name)
        or not isinstance(row, dict)
        or set(row) != {"kind"}
        or row["kind"] not in {"path", "literal"}
        for name, row in manifest["placeholders"].items()
    ):
        raise ValueError("Invalid public placeholder declaration")
    before = assets._tree(output)
    expected = {"files/" + assets._relative(name) for name in manifest["files"]}
    expected.add("portable-review.pending.json" if pending else MANIFEST)
    if set(before) != expected:
        raise ValueError("Portable review membership differs from its manifest")
    if bindings is not None:
        bindings = _bindings(bindings)
        if {name: {"kind": row["kind"]} for name, row in bindings.items()} != manifest[
            "placeholders"
        ]:
            raise ValueError("Private binding names or kinds differ")
        source_before = assets._tree(source)
        if set(source_before) != set(manifest["files"]):
            raise ValueError("Original evidence membership differs")
    changed = 0
    for name, row in manifest["files"].items():
        assets._record(row)
        assets._record({"sha256": row.get("source_sha256"), "bytes": row.get("source_bytes")})
        if row.get("method") not in METHODS:
            raise ValueError("Unknown review transformation")
        if (output / "files" / name).stat().st_mode & 0o111:
            raise ValueError("Review files must not be executable")
        raw = _read(output / "files" / name)
        if _sha(raw) != row["sha256"] or len(raw) != row["bytes"]:
            raise ValueError("Portable review file changed")
        same = row["source_sha256"] == row["sha256"] and row["source_bytes"] == row["bytes"]
        if (row.get("method") == "unchanged") != same:
            raise ValueError("Portable review derivation label differs from its hashes")
        changed += not same
        if source is not None:
            original = _read(source / name)
            if _sha(original) != row["source_sha256"] or len(original) != row["source_bytes"]:
                raise ValueError("Original evidence differs from the recorded source hash")
            transformed, method = _transform(original, name, bindings)
            if transformed != raw or method != row["method"]:
                raise ValueError("Portable review transformation differs")
    if _source_inventory(manifest["files"]) != manifest.get("source_inventory_sha256"):
        raise ValueError("Original evidence inventory hash differs")
    if assets._tree(output) != before or (
        source is not None and assets._tree(source) != source_before
    ):
        raise ValueError("Evidence changed during verification")
    return {
        "verified": True,
        "files": len(manifest["files"]),
        "changed_files": changed,
        "source_and_transforms_verified": source is not None,
        "role": "derived_review_only",
    }


def verify_review(output: Path, *, source: Path | None = None, bindings: dict | None = None):
    manifest = assets._json(_read(output / MANIFEST, assets.MAX_INVENTORY_BYTES))
    return _inspect(output, manifest, source=source, bindings=bindings)


def render_file(review: Path, name: str, output: Path, bindings: dict):
    """Render one verified inspection template locally; never launch an archived command."""
    verify_review(review)
    if output.resolve().is_relative_to(review.resolve()):
        raise ValueError("Rendered files must be outside the review copy")
    name = assets._relative(name)
    manifest = assets._json(_read(review / MANIFEST, assets.MAX_INVENTORY_BYTES))
    if name not in manifest["files"]:
        raise ValueError("Unknown review file")
    bindings = _bindings(bindings)
    if {key: {"kind": row["kind"]} for key, row in bindings.items()} != manifest["placeholders"]:
        raise ValueError("Supply the declared placeholder names and kinds")
    raw = _read(review / "files" / name)
    if _sha(raw) != manifest["files"][name]["sha256"]:
        raise ValueError("Review template changed")
    text = raw.decode("utf-8")
    values = {_token(key): row["value"] for key, row in bindings.items()}
    expression = re.compile("|".join(re.escape(key) for key in values))

    def replace(text):
        return expression.sub(lambda match: values[match[0]], text)

    suffix = Path(name).suffix.lower()
    if suffix in {".json", ".yaml", ".yml"}:
        text = assets._encoded(_strings(assets._json(raw), replace)).decode()
    elif suffix == ".jsonl":
        text = "".join(
            json.dumps(_strings(assets._json(line.encode()), replace), ensure_ascii=True) + "\n"
            for line in text.splitlines()
            if line.strip()
        )
    elif suffix == ".xml":
        root = fromstring(raw)
        for item in root.iter():
            item.attrib.update({key: replace(value) for key, value in item.attrib.items()})
            for key in ("text", "tail"):
                if getattr(item, key):
                    setattr(item, key, replace(getattr(item, key)))
        text = ET.tostring(root, encoding="unicode")
    else:
        text = replace(text)
    if TOKEN_PREFIX in text:
        raise ValueError("Unresolved review placeholder")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(text.encode())
    return {"rendered": True, "executed": False, "bytes": len(text.encode())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("source", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument("--bindings", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("review", type=Path)
    verify.add_argument("--source", type=Path)
    verify.add_argument("--bindings", type=Path)
    render = commands.add_parser("render")
    render.add_argument("review", type=Path)
    render.add_argument("name")
    render.add_argument("output", type=Path)
    render.add_argument("--bindings", type=Path, required=True)
    args = parser.parse_args()
    bindings = assets._json(_read(args.bindings)) if args.bindings else None
    if args.command == "export":
        result = export_review(args.source, args.output, bindings)
    elif args.command == "verify":
        result = verify_review(args.review, source=args.source, bindings=bindings)
    else:
        result = render_file(args.review, args.name, args.output, bindings)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
