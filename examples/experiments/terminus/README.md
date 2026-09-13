The baseline is Harbor Terminus-2 from version 0.23.0. The exporter copies its
complete loop and JSON/XML/timeout prompts into one editable Python file, checks
their hashes, includes the Apache-2.0 license and writes provenance beside it.
The terminal behavior, parsing, context summarization and completion confirmation
remain in upstream code. This is not the paper's exact historical environment
or an implementation of its outer search loop.

The adapter changes the model transport to the controller's Responses pipe and
runs environment commands locally inside the assigned container. It sends full
explicit conversation history; provider-side response references and provider
reasoning state are not reused. The configured input admission bound supplies
Terminus-2's context threshold, not a claimed native model capacity. Output and
input limits, fixed model settings and the shared orchestration budget remain
external. Provider HTTP errors currently fail the program transport; they are not
translated into native provider exception types for Terminus-2's error recovery.
Token costs in the agent trace use the controller's rate card, which
charges cached input at the full input rate; controller records are authoritative.

The pinned task image uses Debian bookworm. It adds Harbor's pinned Python
dependencies, [tmux 3.3a-3](https://packages.debian.org/bookworm/tmux) and
[asciinema 2.2.0-1](https://packages.debian.org/bookworm/asciinema).
Tokenizer assets are cached during the networked build. The task
and verifier run without networking. Apt transitive dependencies are resolved
during the build; package versions and image IDs are retained, but the image is
not yet exported as an immutable reproducibility artifact. The verifier and test
assertions remain those of the earlier pilot. Adding dependencies changes the
candidate environment and needs a new curator decision before measured work.

Status: proposed and unmeasured. Human curation must cover this environment,
benchmark stability, model, budget and comparison protocol. No automatic acceptance
or paid execution is performed by the baseline exporter.
