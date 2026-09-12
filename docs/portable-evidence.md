# Portable evidence review copies

Use `research_harness.evaluation.portable_evidence` to create a separately
labelled review copy with configured operator paths replaced by named
placeholders and JUnit hostnames removed. It retains every source file and
records both original and exported hashes in `portable-review.json`.
Authoritative run directories, runtime proofs, frozen bundles and ledgers stay
unchanged.

The output is **derived review evidence**. Changing a configuration or report
can invalidate its original embedded bundle/runtime hashes; the exporter does
not rewrite those proofs. Use the exact original artifacts for domain audits,
resumption or budget recovery. A review copy is not an approved proposer input.

## Export with private bindings

Create a JSON binding file outside Git, readable only by its owner (`chmod 600`).
Use the actual paths and identifiers to remove. The following values are
synthetic examples:

~~~json
{
  "REPOSITORY": {"kind": "path", "value": "/srv/operator/researchharness"},
  "PYTHON": {"kind": "path", "value": "/srv/operator/researchharness/.venv/bin/python"},
  "RUN": {"kind": "path", "value": "/srv/operator/closed-run"},
  "OPERATOR_HOME": {"kind": "path", "value": "/srv/operator"},
  "HOST": {"kind": "literal", "value": "research-workstation.example"}
}
~~~

Names are uppercase identifiers. Supply 1–64 distinct bindings. Paths must
identify an absolute directory or file; broad roots and parent traversal are
refused. The longest matching path wins, and directory boundaries prevent
replacing a neighboring path with a shared prefix. A repository binding becomes
`${RH_REVIEW_REPOSITORY}`. The public manifest contains names and kinds, never
the binding values. Do not commit the private binding file.

~~~sh
uv run --locked --extra mcp python -m research_harness.evaluation.portable_evidence export \
  /path/to/closed-originals /path/to/new-review \
  --bindings /path/to/private-bindings.json

uv run --locked --extra mcp python -m research_harness.evaluation.portable_evidence verify \
  /path/to/new-review \
  --source /path/to/closed-originals --bindings /path/to/private-bindings.json
~~~

The output must be new and outside the source. Its directory is created with mode
`0700`; exported files under `files/` are `0600` and never executable. Each
manifest entry records the original and exported SHA-256, byte lengths and
transformation method. Verification with **both** originals and private
bindings checks every original hash and independently repeats each
transformation. Verification without those arguments checks the copy against
its manifest, but does not prove its provenance or identity removal. The result
states this distinction in `source_and_transforms_verified`.

JSON, JSON-compatible YAML and JSONL are transformed through decoded strings,
including escaped paths. Ordinary UTF-8 text uses literal replacement. XML is
parsed with external entities disabled; only JUnit `testsuite`/`testsuites`
documents lose hostname attributes. Unchanged files retain their exact bytes.
Partial JSON/JSONL and non-JSON YAML use text replacement when parsing fails.
Binary files are preserved only when the configured identity scan finds no
match; otherwise the export fails. Nested archives, linked files, unsafe paths,
configured identity in filenames and source changes also fail.

Limits are 64 MiB per file, 100,000 tree entries and 16 GiB total input. Nothing
is silently dropped or truncated. Failed exports can leave a private partial
directory, but never the final `portable-review.json`; retain it for diagnosis
and use a fresh output directory next time.

This is a **known-identifier transformation**, not a general secrets or personal
information detector. The residual scan covers configured UTF-8/UTF-16 and
common JSON-escaped values; it does not guarantee discovery of unknown names,
encoded secrets, percent-encoded or base64 content. Manually review the copy
before publishing, including whether its original hashes disclose sensitive
relationships. Keep held-out content and host-only records outside the export.

## Render a local inspection template

Create another private binding file with the same names and kinds, using paths
on the destination machine. Render one indexed file into a new location:

~~~sh
uv run --locked --extra mcp python -m research_harness.evaluation.portable_evidence render \
  /path/to/new-review comparison/omnigent/cases/controlled-policy/runtime/agent/tools/mcp/research.yaml \
  /path/to/new-local-template.json --bindings /path/to/destination-bindings.json
~~~

Rendering verifies the review copy first, performs structural JSON/XML escaping
where applicable, and refuses overwrite or unresolved placeholders. JSON,
JSON-compatible YAML and JSONL must be complete and parseable for rendering;
partial captures and general YAML remain available for inspection. The command
never executes the file or launches an archived command. Inspect configurations
before using them in a separately prepared run. Rendered files cannot relocate
or resume an original budgeted run or reset its ledger.

## Checkpoint and publication status

### Verify a historical archive on another machine

Historical compressed files now live at their pinned Git revision. First
[restore the exact originals](evidence-retention.md#restore-exact-historical-originals)
into a fresh private directory. Use the archive below its original repository
relative path there, and the unchanged `index.json` in the current checkout.
The reference catalog preserves the original compressed hashes; it does not
regenerate or sanitize the archives.

Historical checkpoints use a flat `index.json` with an `archive_sha256` and
per-file `files` mapping. The command below verifies the original compressed
bytes and every regular member without extracting files, importing archived
code, changing paths or opening a live run. Use the committed index from the
same checkpoint as the archive; a rewritten index is not an independent trust
anchor. The newer release format uses the retention verifier instead.

~~~sh
uv run --locked python - /path/to/index.json /path/to/artifacts.tar.gz <<'PY'
import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

index = json.loads(Path(sys.argv[1]).read_bytes())
archive = Path(sys.argv[2])
with archive.open("rb") as stream:
    archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
if archive_sha256 != index["archive_sha256"]:
    raise ValueError("Original compressed archive hash differs")
seen = set()
total_bytes = 0
with tarfile.open(archive, "r|gz") as members:
    for member in members:
        if member.isdir():
            continue
        name = member.name
        path = PurePosixPath(name)
        if (not member.isfile() or path.is_absolute() or ".." in path.parts
                or "\\" in name or name in seen or name not in index["files"]):
            raise ValueError("Unexpected, unsafe or duplicate archive member")
        total_bytes += member.size
        if total_bytes > 16 * 1024**3 or len(seen) >= 100000:
            raise ValueError("Historical verification limit exceeded")
        with members.extractfile(member) as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != index["files"][name]:
            raise ValueError("Original member hash differs: " + name)
        seen.add(name)
if seen != set(index["files"]):
    raise ValueError("Original archive membership differs")
print(json.dumps({"archive_sha256": archive_sha256, "files_verified": len(seen),
                  "original_bytes": total_bytes, "extracted": False}, indent=2))
PY
~~~

This establishes integrity of the published subset only. The budgeted-search
archive's `verification-tools/audit-budgeted-search.py` was a check of the
**original local run**: it requires private final executions, the retained
ledger/registry and the executed source version, and writes its report inside
that run. Those private inputs are intentionally absent from the public
archive. Editing its hard-coded paths cannot reconstruct them, and a current
checkout cannot substitute for its frozen source. Do not run that script against
a derived review copy or present member-integrity verification as a reproduction
of the full original audit. The historical script and its hashes stay unchanged.

### Derived review checkpoint

The September 12 representative check uses the complete 159-file historical
controlled-runtime archive, first verified against its committed archive and
member hashes. Three path-bearing files change; 156 remain byte-identical.
The [checkpoint record](../examples/evaluation/evidence/portable-review-2026-09-12/README.md)
records the source, exact hashes and verification results. This is a current
tooling check on historical synthetic runtime evidence, not a new research run.
JUnit hostname removal is separately covered by regression tests.

The subsequent [complete baseline review](../examples/evaluation/evidence/historical-review-all-2026-09-12/README.md)
covers all 150 Git files under both evidence roots at the historical baseline.
Its preparation recipe verifies eleven top-level tar archives and two gzip JSON
captures, then decodes 544 nested Omnigent snapshot archives while retaining
container/member provenance. All 15,395 leaf files plus provenance enter the
review. Source-bound verification changes 124 files and preserves 15,272 exactly.
The prepared 15,397-member release package includes the review manifest; a fresh
extraction passes every member hash and finds no configured identity matches or
nonempty JUnit hostname attributes. All forty binary files remain intact.
These checks cover the complete baseline, including repeated snapshots; they do
not establish general secret detection or authorize publication.

Package the complete review directory using the
[retention tools](evidence-retention.md). Retain the original archive hash and
source reference separately from the revision of the export tools. A prepared
asset index describes intended destinations; only an actual verified download
establishes availability.

GitHub reported this repository public on September 12, 2026. Historical
originals still contain operator paths in Git history and restored copies.
The retention migration removes 25 originals from the current checkout while
keeping their exact hashes and a verified restoration path. It does not erase
their public history. Local content checks now cover the complete baseline review;
RW-5/RW-6 delivery remains open until the reviewed replacement assets are published
and a download is verified. Extracting unsanitized historical originals still
exposes their original paths. No history or original runtime bytes are rewritten.
