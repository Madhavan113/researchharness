"""Authored observation strategy example; no optimization/performance claim."""


def apply(event):
    if event["kind"] != "observation":
        raise ValueError("This example enables observation projection only")
    payload = event["payload"]
    items = payload["items"]
    ranked = sorted(
        items,
        key=lambda item: str(item["value"].get("title", item["value"].get("url", ""))),
        reverse=True,
    )
    rendered = [
        {
            "text": "Candidate view: "
            + str(item["value"].get("title", item["value"].get("url", "Source")))[:500],
            "references": [item["id"]],
        }
        for item in ranked
    ]
    return {
        "decision": {
            "order": [item["id"] for item in ranked],
            "rendered": rendered,
            "replace_body": bool(rendered),
            "stop_recommended": not items,
            "stop_reason": "No supplied results" if not items else "",
        },
        "state": {"observations": event["state"].get("observations", 0) + 1},
    }
