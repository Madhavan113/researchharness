"""Grade saved discovery runs against independently reviewed JSON requirements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_harness.evaluation import frontier, score


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specification", type=Path, help="Independent JSON requirements")
    parser.add_argument("artifacts", type=Path, nargs="+", help="Discovery output directories")
    parser.add_argument(
        "--runtime-usage",
        type=Path,
        help="Host-owned discovery usage export; requires exactly one artifact directory",
    )
    args = parser.parse_args(argv)
    if args.runtime_usage is not None and len(args.artifacts) != 1:
        parser.error("--runtime-usage requires exactly one artifact directory")
    try:
        specification = json.loads(args.specification.read_text(encoding="utf-8"))
        results = [
            score(path, specification, runtime_usage=args.runtime_usage) for path in args.artifacts
        ]
        print(
            json.dumps(
                {"results": results, "frontier": frontier(results)}, indent=2, allow_nan=False
            )
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")


if __name__ == "__main__":
    main()
