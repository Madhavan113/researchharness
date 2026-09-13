# Checkpoint evidence retention

New complete checkpoint archives belong in GitHub release assets. Git retains
the small `index.json`, acceptance/verification summaries and a README explaining
the run, limitations, source revision, asset location and verification command.
Each new evidence checkpoint directory must total **less than 1,000,000 bytes**
in Git. This keeps complete traces available without adding another archive to
every code checkpoint.

This policy applies to future checkpoints. The
[legacy inventory](../examples/evaluation/evidence-policy.json) records the
13 existing compressed artifacts by exact SHA-256 and the byte sizes of the
18 historical checkpoints at commit
`b58bf44a84407d1069039499411e53aaaede5968`. Existing bytes and historical hashes
remain intact. Historical directories may add less than 1,000,000 bytes beyond
that fixed baseline, but cannot replace or add compressed artifacts. Updating
the inventory is an explicit policy change, not part of creating a checkpoint.

## Prepare an archive

First create a dedicated, closed export directory containing the complete
evidence selected for sharing. Keep the authoritative original run, ledger and
private final package in their original locations. Review the export for
credentials, operator identity, private held-out contents and host-only records.
The packager preserves file bytes; it does not sanitize them or decide what is
appropriate to publish. RW-5's path/hostname cleanup remains separate.

Record the commit corresponding to the executed source, and retain observed
source fingerprints in the verification summary. The packager checks that this
commit exists in the supplied checkout; it does not prove that the run executed
that revision. Review the observed fingerprints before making that claim.
Do not copy a redundant top-level `checkpoint-source/`: retrieve that source
from its commit. Preserve frozen implementation files that an original runtime
proof actually requires; the tool never silently drops files from the export.

~~~sh
uv run --locked --extra mcp python -m research_harness.evaluation.evidence_assets prepare \
  /path/to/reviewed-export /path/to/new-assets \
  --repository Madhavan113/researchharness \
  --source-repository /path/to/researchharness \
  --source-commit FULL_EXECUTED_SOURCE_COMMIT \
  --tag evidence-CHECKPOINT
~~~

Use a new output directory outside the export. The tool creates a private `0700`
output directory and produces:

- `artifacts.tar.gz`: every regular input file, with deterministic ordering and
  normalized tar owner/group names, timestamps and executable/nonexecutable modes.
- `files.json.gz`: the complete member inventory with byte lengths, modes and
  SHA-256 hashes. This is also an external asset, so a large inventory does not
  defeat the Git size limit.
- `index.json`: small metadata binding both compressed asset hashes and lengths,
  the source commit, release destinations, member count and expanded byte total.

Symbolic/hard links, path escapes, inaccessible directories, changed input
files and exceeded limits fail preparation. Limits are 100,000 tree entries,
16 GiB of input and 32 MiB of expanded inventory; failure never means truncated
evidence. Empty directories are not retained. Existing output directories are
never overwritten. Failed preparation may retain partial assets or
`index.pending.json`; only a successfully verified package gets `index.json`.
Keep failed outputs for diagnosis and use a fresh directory on the next attempt.

`availability: prepared` means the URLs are intended destinations. The command
makes no network request, creates no release and does not establish that assets
are available on GitHub. Copy only the small index and summaries into the new
Git checkpoint directory, alongside its README. Assets remain outside Git.

## Publish and verify availability

Publish the reviewed archive and inventory together under the recorded release
tag in the recorded repository. Keep that tag and those asset bytes stable;
corrections require a new checkpoint/tag. Keep private repositories private.
The release must retain both assets for as long as the checkpoint is supported.

After publication, use an authenticated GitHub CLI to download both assets into
a new directory. Use the exact tag from the index; never substitute `latest`.

~~~sh
gh release download evidence-CHECKPOINT \
  --repo Madhavan113/researchharness \
  --pattern artifacts.tar.gz --pattern files.json.gz \
  --dir /path/to/new-download

uv run --locked --extra mcp python -m research_harness.evaluation.evidence_assets verify \
  examples/evaluation/evidence/CHECKPOINT/index.json \
  /path/to/new-download/artifacts.tar.gz \
  /path/to/new-download/files.json.gz
~~~

The verifier streams both compressed hashes and checks every archive member
against the bound inventory without extracting or executing anything. Changed
hashes, missing/extra/duplicate members, links, unsafe paths, unexpected modes,
unnormalized tar host metadata and decompression bounds fail verification.
Retain the actual download command, release URL, verification result and date in
the README or verification summary. Only after this round trip may the index's
availability be recorded as `download_verified`; the field is an operator
record, not independent authentication of remote storage.

These checks establish file integrity and completeness against the committed
index. They do not validate research quality, spending approval or runtime
results. Run the relevant domain audit using the pinned source as well. A
publication asset is not automatically a safe proposer input: preserve the
original feedback/private-final boundary.

## Check Git retention locally and in CI

~~~sh
uv run --locked --extra mcp python -m research_harness.evaluation.evidence_assets check-policy \
  --repo . --policy examples/evaluation/evidence-policy.json
~~~

The ordinary CI job runs this check. It examines tracked files and visible
untracked files under the two evidence roots, requires a README for each new
checkpoint, enforces its total byte allowance and validates new release indices.
New compressed blobs are refused by extension or common archive signatures,
including archives renamed as text or force-added past `.gitignore`. Historical
compressed blobs must retain their exact hashes. Ignore rules help prevent
accidental additions; the CI check enforces the policy on committed files.

The first published checkpoint using external assets still needs a real upload
and verified download. Local packaging tests and the small RW-13 report checkpoint
do not establish that delivery step or remove historical operator paths.
