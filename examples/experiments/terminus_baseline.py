"""Create a proposed Terminus-2 experiment and an editable baseline; no execution or acceptance."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from research_harness.util import write_json


def prepare_baseline(output: Path, harbor_python: Path):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parent
    subprocess.run(
        [
            str(harbor_python.absolute()),
            "-m",
            "research_harness.experiments.terminus",
            "--out",
            str(output / "candidate"),
        ],
        env={**os.environ, "PYTHONPATH": str(source.parents[1] / "src")},
        check=True,
        timeout=30,
    )
    proposal = output / "proposal"
    shutil.copytree(source / "meta-harness", proposal)
    spec = json.loads((proposal / "experiment.json").read_bytes())
    spec["id"] = "meta-harness-terminus-pilot"
    write_json(proposal / "experiment.json", spec)
    environment = proposal / "overlays/cancel-async-tasks/environment"
    shutil.copyfile(source / "terminus/Dockerfile", environment / "Dockerfile")
    shutil.copyfile(source / "terminus/requirements.txt", environment / "requirements.txt")
    with (proposal / "plan.md").open("a") as stream:
        stream.write(
            "\n## Exported Terminus-2 baseline\n\n" + (source / "terminus/README.md").read_text()
        )
        stream.write(
            "\nExport provenance (included in the reviewed input fingerprint):\n\n```json\n"
            + (output / "candidate/baseline.json").read_text()
            + "\n```\n"
        )
    return proposal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--harbor-python", type=Path, required=True)
    args = parser.parse_args()
    print(prepare_baseline(args.out, args.harbor_python))


if __name__ == "__main__":
    main()
