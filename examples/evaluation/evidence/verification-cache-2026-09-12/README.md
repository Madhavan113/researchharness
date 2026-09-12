# Artifact verification fixture, September 12, 2026

These small reports record an offline workspace verification measurement.
[Before](before.json) uses PR #14 head
`4730b582c07c4e8f01857e57ec3bcc18403efc04`;
[after](after.json) uses the implementation hashes recorded in that report.
Both use the exact same [fixture](../../verification_cache_fixture.py), Python
version and sixteen deterministic 2-MiB files, plus their readable private copy.
The fixture hash is
`18f46c14f19e437e09cea2af81e8bbf20ad4a2349d459826e993bcab3b146264`.

| Eight warmed, unchanged listings | Before | After |
| --- | ---: | ---: |
| Full artifact read opens | 256 | 0 |
| Bytes represented by those reads | 536,870,912 | 0 |
| Elapsed seconds | 0.219779 | 0.012375 |

Metadata scans run on every call. Setup, initial verification and final closure
are outside the measured interval; closure rereads the files and verifies
quiescence and removal of the owned copy. Both runs made zero model requests
and zero Docker executions. Timing is one warm filesystem observation, not a
disk-throughput or model-efficiency claim; tests assert integrity and read reuse
without a wall-clock speed threshold.

Reproduce with a new output directory:

~~~sh
uv run --locked --extra mcp python examples/evaluation/verification_cache_fixture.py \
  --out /tmp/research-verification-fixture
~~~

The output retains the deterministic source files and `report.json`. Only the
small reports are checked in; binary feedback trees and host-specific workspace
records remain outside Git. To reproduce the baseline, run this same fixture
from a checkout of the before revision. Compare the report's implementation
hashes before interpreting timing differences.
