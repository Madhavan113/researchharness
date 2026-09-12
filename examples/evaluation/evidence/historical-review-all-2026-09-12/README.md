# Complete historical evidence review

This checkpoint prepares a separately labelled review copy of **all 150 Git files**
under both evidence roots at commit
`b58bf44a84407d1069039499411e53aaaede5968`. The original baseline is 27,209,764
bytes. Eleven tar archives, two gzip JSON captures and 544 nested Omnigent
snapshot archives are decoded before review. Original bytes remain in Git;
no archived code is executed.

The complete private input tree has 15,395 leaf files plus `historical-source.json`.
That provenance file binds every original Git file, compressed container, archive
member and directory entry to the decoded inputs. Directory entries are recorded
as metadata; tar ownership/timestamps are not replayed. The 544 nested containers
contain 31 distinct compressed payloads. Their contents are retained at every
original location, including repeated development-feedback snapshots.

The portable exporter changes 124 files: 113 JSON files, two text files and nine
JUnit reports. The other 15,272 files retain their exact bytes, including the
provenance record. Forty binary files are retained without configured identity
matches. A separate verification process repeats every transformation against
the private originals and bindings. This is a known-identifier check, not a
general secrets detector or a new research run.

## Reproduce the inputs and review

Use a checkout containing the pinned baseline and this recipe. Keep every output
directory new and outside the repository. The recipe is deliberately bound to
the 150-file baseline; it checks the original compressed/member hashes and
refuses incomplete membership, unsupported members, collisions and exceeded
bounds. Failed attempts can leave private partial output; use a fresh directory
for the next attempt. Files are nonexecutable and readable only by their owner.

~~~sh
uv run --locked python examples/evaluation/evidence/historical-review-all-2026-09-12/prepare_originals.py \
  --repo . --out /path/to/new-private-originals

uv run --locked --extra mcp python -m research_harness.evaluation.portable_evidence export \
  /path/to/new-private-originals /path/to/new-review \
  --bindings /path/to/private-bindings.json

uv run --locked --extra mcp python -m research_harness.evaluation.portable_evidence verify \
  /path/to/new-review --source /path/to/new-private-originals \
  --bindings /path/to/private-bindings.json
~~~

Follow the [binding guide](../../../../docs/portable-evidence.md#export-with-private-bindings)
to configure the original operator paths and hostname privately. This checkpoint
uses `REPOSITORY`, `PYTHON`, `RUN`, `OPERATOR_HOME` and `HOST`; their values are
not committed. Top-level tar members appear below the original file path plus
`.members/`. Gzip JSON appears below `.decoded/model-requests.json`. Nested tar
members use another `.members/` component, with the exact original member name
retained in provenance.

## Publication and limits

The [index](index.json) binds the complete 15,397-member release package:
140,767,556 expanded bytes, a 24,371,182-byte archive and a 603,906-byte member
inventory. The [acceptance](acceptance.json) and [verification](verification.json)
record all counts, original/derived provenance, exact commands and report hashes.
A fresh private extraction checked every member hash, all normalized tar host
metadata, zero configured identity matches and all nine JUnit reports.

| Prepared asset | SHA-256 |
| --- | --- |
| `artifacts.tar.gz` | `68f005af1e2107dbcc1ee3a7666cf01262ed82451c7dd29be8970617fee040f0` |
| `files.json.gz` | `1a541a21ee8d86bc6d55229410850ea9342ca7e34cb57675590af99ea6011c3a` |

The complete archive and member inventory are prepared outside Git. Publication
to this public repository still requires the pending user decision; there is no
upload or download receipt. Only the recipe, small index and verification
summaries belong in this checkpoint under the
[retention policy](../../../../docs/evidence-retention.md).

The intended tag is `evidence-historical-review-all-2026-09-12`; the index remains
`availability: prepared`. After an approved upload, retrieve those exact assets
and verify the downloaded bytes, without substituting a newer release:

~~~sh
gh release download evidence-historical-review-all-2026-09-12 \
  --repo Madhavan113/researchharness \
  --pattern artifacts.tar.gz --pattern files.json.gz --dir /path/to/new-download

uv run --locked --extra mcp python -m research_harness.evaluation.evidence_assets verify \
  examples/evaluation/evidence/historical-review-all-2026-09-12/index.json \
  /path/to/new-download/artifacts.tar.gz /path/to/new-download/files.json.gz
~~~

Derived files retain original embedded proof hashes, which can no longer validate
changed configurations. Use exact originals for runtime audits and accounting;
this review copy cannot resume a budgeted run or replace its ledger. The historical
audit script also needs private final-run inputs and matching source that are not
supplied by a review copy. See the
[historical audit boundary](../../../../docs/portable-evidence.md#verify-a-historical-archive-on-another-machine).
No private held-out package is added, no benchmark review is claimed, and no model
quality or optimization result follows from this packaging check. Historical Git
objects and privately restored originals retain their original identities.
