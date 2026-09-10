"""Standalone container entrypoint; never import a candidate in the host process."""

import contextlib
import json
import runpy
import sys


def main():
    with open("/input/request.json", encoding="utf-8") as handle:
        request = json.load(handle)
    with contextlib.redirect_stdout(sys.stderr):
        module = runpy.run_path("/input/strategy.py")
        operation = module.get("apply")
        if not callable(operation):
            raise ValueError("A strategy must define apply(event)")
        decision = operation(request)
    if not isinstance(decision, dict):
        raise ValueError("Strategy output must be a JSON object")
    print(json.dumps(decision, allow_nan=False, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
