# Budgeted strategy search checkpoint

[Acceptance](acceptance.json) records a passing three-iteration search and isolated final evaluation through the actual coding proposer, Docker and normal Omnigent/MCP runtime. All 125 synthetic requests were reserved before HTTP dispatch and settled against raw usage, with zero remaining holds. There were 13,750 synthetic tokens and 157 Docker executions. These fixture amounts do not represent money spent or measured model improvement.

[Verification](verification.json) retains the exact runtime command, the [1,004-test report](https://github.com/Madhavan113/researchharness/blob/b58bf44a84407d1069039499411e53aaaede5968/examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/pytest.xml), the separate [54-test report](https://github.com/Madhavan113/researchharness/blob/b58bf44a84407d1069039499411e53aaaede5968/examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/focused-tests.xml), source hashes and lint/format results. The [fresh-process audit](independent-audit.json) matched seventeen gateway archives, their usage and settlement evidence to the ledger.

The [archive index](index.json) lists all 6,479 payload hashes in `artifacts.tar.gz`:

- `development-feedback/` and `search/proposal-inputs/`: complete development artifacts and exact proposer input snapshots.
- `search/proposals/`: sealed attempts, raw gateway traffic, tool results, generated candidate programs and actual Docker checks.
- `search/interfaces/`, `search/candidate-attempts/` and `baseline/`: public contract checks, admitted candidate artifacts and the original baseline.
- `search/budget/proposers/` and `host-budget/`: host accounting receipts, the fixture ledger/registry and observations of reservations before dispatch.
- `checkpoint-source/` and `executed-fixture-source/`: current source and exact executed fixture source, with separate hashes.
- `host-metadata/` and `verification-tools/`: an explicitly labeled journal excerpt, the audit program and the initial run diagnostic/source.

The first runtime attempt completed all search/final executions and settlements, then failed in the example's event-list verification. After correcting that check, the entire example passed in a fresh fixture directory. Production/test bytes remain those covered by the full suite. A formatting-only change after the accepted run preserves the same Python AST; both byte versions are retained. The complete initial run remains local at `/tmp/rh-budgeted-strategy-search-20260909/`.

Private final packages/executions and the separate representative held-out draft are excluded. Native runtime credentials are excluded. Only the original development snapshots are valid proposer inputs; source, host metadata and accounting artifacts are for reviewers. All benchmark cases remain authored pending independent human review, and measured model evaluation remains outstanding.

For a portable, read-only check of the published bytes, use the
[historical archive verification command](../../../../docs/portable-evidence.md#verify-a-historical-archive-on-another-machine).
It verifies the original archive hash and all 6,479 member hashes without
extracting or executing anything. The archived `audit-budgeted-search.py` checked
the original local run, including private final execution inputs omitted here;
it is not a standalone verifier of this public subset. Changing its hard-coded
paths does not supply those missing inputs or the matching executed source.
Historical original bytes and the original audit result remain unchanged.

## Historical original files

The following originals are retained unchanged at Git commit
`b58bf44a84407d1069039499411e53aaaede5968` and referenced by
the [hash and byte-length catalog](../../../../examples/evaluation/evidence-policy.json):

- `artifacts.tar.gz`
- `focused-tests.xml`
- `pytest.xml`

Use the [verified restoration command](../../../../docs/evidence-retention.md#restore-exact-historical-originals)
from the repository root to retrieve all catalogued originals into a new private
directory, or select these files with `--file` and their full repository paths.
This checkpoint's restored files appear under
`examples/evaluation/evidence/budgeted-strategy-search-2026-09-09/` below that output directory.
Current summaries and original hash indices remain in this checkout.
Restoration verifies exact historical bytes and never executes them. Originals
retain any recorded machine paths; they are not portable review copies or proof
that an archived run can be resumed. Git history has not been rewritten.
