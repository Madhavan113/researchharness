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

The September 12 representative check uses the complete 159-file historical
controlled-runtime archive, first verified against its committed archive and
member hashes. Three path-bearing files change; 156 remain byte-identical.
The [checkpoint record](../examples/evaluation/evidence/portable-review-2026-09-12/README.md)
records the source, exact hashes and verification results. This is a current
tooling check on historical synthetic runtime evidence, not a new research run.
JUnit hostname removal is separately covered by regression tests.

Package the complete review directory using the
[retention tools](evidence-retention.md). Retain the original archive hash and
source reference separately from the revision of the export tools. A prepared
asset index describes intended destinations; only an actual verified download
establishes availability.

GitHub reported this repository public on September 12, 2026. Historical
originals still contain operator paths in the current tree and Git history.
This derived export does not remove that exposure or complete RW-5's original
repository-wide acceptance check. No history or original runtime bytes are
rewritten by these tools.
