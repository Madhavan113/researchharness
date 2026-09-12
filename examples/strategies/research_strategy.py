"""Authored observation/context strategy; no optimization/performance claim."""


def apply(event):
    if event["kind"] == "context":
        return {
            "decision": {"keep_group_ids": event["payload"]["required_group_ids"]},
            "state": {
                **event["state"],
                "context_requests": event["state"].get("context_requests", 0) + 1,
            },
        }
    if event["kind"] != "observation":
        raise ValueError("Unsupported strategy event")
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
        "state": {**event["state"], "observations": event["state"].get("observations", 0) + 1},
    }
