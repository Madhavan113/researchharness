"""Export the pinned Harbor Terminus-2 loop as one editable Python program.

Run this exporter with the separate Harbor interpreter. It reads installed
source and templates without importing or executing the candidate program.
"""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

from research_harness.util import write_json

VERSION = "0.23.0"
SOURCE_SHA256 = "00105976f913b49ba3d2074562adbd9cde7b8882a5aaf1f2dbb2a22204644e31"
TEMPLATE_SHA256 = {
    "terminus-json-plain.txt": "89a3dc3a15752b748a99fb907c9251ab31a464e13386583fb48476b078b54a34",
    "terminus-xml-plain.txt": "721c0ada54921cbb7bb9f817f2306c79ff4ba3381d08be7eb97fbeb0d99d298d",
    "timeout.txt": "32bf9aa7b157a6a0e0ba0d33b81e59dd67204276f158afc1fe6cb684ced68872",
}


def export(output: Path) -> dict:
    distribution = importlib.metadata.distribution("harbor")
    if distribution.version != VERSION:
        raise ValueError(f"Baseline export requires Harbor {VERSION}")
    root = Path(distribution.locate_file("harbor/agents/terminus_2"))
    source = (root / "terminus_2.py").read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("The installed Terminus-2 source differs from the pinned baseline")
    templates = {path.name: path.read_text() for path in sorted((root / "templates").glob("*.txt"))}
    if {
        name: hashlib.sha256(text.encode()).hexdigest() for name, text in templates.items()
    } != TEMPLATE_SHA256:
        raise ValueError("The installed Terminus-2 prompts differ from the pinned baseline")
    adapter = Path(__file__).with_name("terminus_runtime.py").read_bytes()
    license_path = next(
        path for path in distribution.files if str(path).endswith("/licenses/LICENSE")
    )
    license_text = Path(distribution.locate_file(license_path)).read_bytes()
    prefix = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "# Harbor Terminus-2 0.23.0, with Research Harness transport/environment adapter.\n"
        "# Upstream loop is unchanged below; the appended adapter changes IO only.\n"
        "import os as _rh_os\nimport sys as _rh_sys\n"
        "_RH_PROTOCOL_OUT = _rh_sys.stdout\n_rh_sys.stdout = _rh_sys.stderr\n"
        "_rh_os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'\n"
        "import importlib.metadata as _rh_metadata\n"
        "if _rh_metadata.version('harbor') != '0.23.0':\n"
        "    raise RuntimeError('The baseline requires Harbor 0.23.0 in the task image')\n"
        f"_RH_TEMPLATES = {templates!r}\n\n"
    ).encode()
    program = prefix + source + b"\n\n# Research Harness runtime adapter follows.\n" + adapter
    program += b"\n\nif __name__ == '__main__':\n    baseline_main(Terminus2, _RH_PROTOCOL_OUT, _RH_TEMPLATES)\n"
    output.mkdir(parents=True, exist_ok=False)
    (output / "agent.py").write_bytes(program)
    (output / "LICENSE").write_bytes(license_text)
    provenance = {
        "baseline": "Harbor Terminus-2",
        "harbor_version": VERSION,
        "upstream_source_sha256": SOURCE_SHA256,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "adapter_sha256": hashlib.sha256(adapter).hexdigest(),
        "template_sha256": {
            name: hashlib.sha256(text.encode()).hexdigest() for name, text in templates.items()
        },
        "license": "Apache-2.0",
        "adaptations": [
            "controller Responses pipe instead of native provider transport",
            "local task-container operations instead of remote environment calls",
            "context threshold uses the controller input admission bound",
        ],
        "measured": False,
    }
    write_json(output / "baseline.json", provenance)
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.out), indent=2))


if __name__ == "__main__":
    main()
