# Historical evidence reference checkpoint

The [acceptance record](acceptance.json) verifies exact restoration of 25 original
files, totaling 22,872,690 bytes, from published commit
`b58bf44a84407d1069039499411e53aaaede5968`. Every file was compared with the
original Git blob, its current-checkout copy before removal, and its catalogued
SHA-256/byte length. The private output directory and nonexecutable file modes
were also checked. Original bytes and historical hashes are unchanged.

The [reference catalog](../../evidence-policy.json) identifies all 13 historical
compressed artifacts and 12 loose path/hostname-bearing files. Their duplicate
current-checkout copies are removed. The
[restoration guide](../../../../docs/evidence-retention.md#restore-exact-historical-originals)
explains how to retrieve all or selected originals into a fresh private directory;
the unchanged checkpoint indices remain available for member verification.

This checkpoint adds only small verification metadata to Git. It creates no new
archive or release asset. Existing Git history remains public and retains the
original operator paths. Restored originals are unsanitized; this is an integrity
check, not execution, runtime resumption or a measured research result. Complete
derived review coverage and the first approved release upload/download remain open.

[Verification](verification.json) records restoration, regression-test and
identity-scan results, with hashes of the local test reports.
