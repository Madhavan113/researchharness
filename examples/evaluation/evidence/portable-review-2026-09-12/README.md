# Portable review export acceptance — September 12, 2026

This checkpoint verifies the portable export tools against the **complete
159-file historical controlled-runtime archive**. It is derived review evidence,
not a new research run. Historical model/source responses are authored fixtures.
The original archive and its embedded runtime proofs remain unchanged.

- [acceptance.json](acceptance.json) binds the original archive/index, both tool
  source hashes, the review manifest and all three changed file names.
- [verification.json](verification.json) records separate-process source-bound
  transformation and archive verification, plus the passing local test reports.
- [index.json](index.json) binds the complete prepared archive and compressed
  member inventory. **Availability is `prepared`: neither asset has been
  uploaded, and no download receipt exists.**

The export uses tools at commit
`cf21dc26c01805001cf1f2687e1f3a3117003613`. Its original input is
[controlled-runtime-2026-09-08](../controlled-runtime-2026-09-08/index.json),
as retained at `e03c498930906b6efa48812168e02780a78a78da`. That reference locates
the historical bytes; it does not claim the September 8 runtime executed the
September 12 code.

## Verified contents

The compressed original SHA-256 is
`30049e5f7b3cb3b902ae3ffd9fb6fc94c1b0e20575a8766e35e22df62652e153`.
All 159 member hashes match its committed index, totaling 1,937,864 bytes.
The review export replaces configured local paths in the MCP configuration,
runtime case record and comparison report. The other **156 files are
byte-identical**, including raw provider evidence. No configured identifier
remains under the exporter's supported scan rules. Private binding values are
retained locally, outside Git.

The complete review archive has **160 regular members**: all 159 exported files
and the 54,204-byte `portable-review.json`. That manifest has SHA-256
`c56d63a4172bbd48553b39014800343683a44a8f260d6752e59fabce18a351b6`.
It records every original/exported hash and marks the content
`derived_review_only`. A fresh CLI process checked all originals and repeated
the transformations; another checked every packaged member and both asset hashes.

| Prepared asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `artifacts.tar.gz` | 338,519 | `8d136fbe7f98c83b5fd1aaa0b36496e0cbfae58491d3a4a5bed0ce47be8f4541` |
| `files.json.gz` | 7,665 | `88a19a5c0bcdfaa38684d7c53121fde135e53b1bb1611f9cca3ce92af4b1b379` |

The required local Docker/Omnigent suite passes **1,444 tests, zero skips**.
The focused portable-export/retention group passes 66 tests, including 32 new
portable-export cases. Ruff lint and formatting pass. JUnit hostname removal,
binary identity rejection and cross-platform template rendering are covered by
fixtures; the representative runtime archive contains no JUnit report.

## Reproduce local verification

Follow the [portable evidence guide](../../../../docs/portable-evidence.md) to
create a fresh review export from closed originals using private named bindings.
For this checkpoint, the original compressed archive and each safely decoded
regular member must first match the original committed index. Do not run code
from an archive to perform that check. Bind the original repository, Python,
run and operator-home paths plus the host identifier; binding values are private
and are not part of this checkpoint.

~~~sh
uv run --locked --extra mcp python -m research_harness.evaluation.portable_evidence verify \
  /path/to/review --source /path/to/originals --bindings /path/to/private-bindings.json

uv run --locked --extra mcp python -m research_harness.evaluation.evidence_assets verify \
  examples/evaluation/evidence/portable-review-2026-09-12/index.json \
  /path/to/prepared-assets/artifacts.tar.gz /path/to/prepared-assets/files.json.gz
~~~

The exact prepared files are currently retained locally. The intended release
tag is `evidence-portable-review-2026-09-12` in `Madhavan113/researchharness`;
the index records the prospective URLs. **The following download command will
work only after those reviewed bytes are published:**

~~~sh
gh release download evidence-portable-review-2026-09-12 \
  --repo Madhavan113/researchharness \
  --pattern artifacts.tar.gz --pattern files.json.gz \
  --dir /path/to/new-download
~~~

Run the asset verifier above against that new download and record the actual
result before changing availability to `download_verified`. The
[retention policy](../../../../docs/evidence-retention.md) defines the procedure.

## Limits

Three changed files still contain their original embedded runtime/bundle hash
references. The exporter does not invent replacement proofs. Use the exact
originals for runtime/domain audits, ledger recovery and resumption; rendered
review templates cannot resume the original run. Hash matching establishes
integrity against the index, not research quality or approval to expose content.

GitHub reports the repository public. Historical originals still contain
operator paths in Git, so RW-5's repository-wide acceptance is not met. The
known-identifier scan is not a general secrets detector. This checkpoint does
not publish release assets, remove historical identity, supply human benchmark
review, authorize spending or establish live model performance.
