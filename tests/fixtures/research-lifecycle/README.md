# Frozen research lifecycle inputs

These four authored cases and their exact source fixtures were copied from
`examples/evaluation/development` at commit
`cdc596c9f7da49d0dff4506c3bec6e2d6eeac928` on September 12, 2026. The manifest
selects only the inputs needed by controller, pilot, final-phase and leakage
tests. Original case and source-fixture hashes are retained.

They exercise software state transitions, evidence binding and failure recovery.
They are not reviewed benchmark cases or model-performance evidence. Keep them
stable when editing the development benchmark; update a snapshot deliberately
when a lifecycle contract changes. Archive lifecycle tests construct their own
two minimal cases. Benchmark and workbook-fixture tests continue to inspect the
actual development package because that package is their subject.
