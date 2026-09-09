"""Authored strategy fixture: stable ranking plus concise observation rendering."""


def apply(event):
    sources = event.get("sources", [])
    ranked = sorted(sources, key=lambda source: (-source.get("priority", 0), source["id"]))
    return {
        "ranked_source_ids": [source["id"] for source in ranked],
        "observations": {source["id"]: f"{source['title']}: {source['url']}" for source in ranked},
        "stop": {"recommend": not ranked, "reason": "No available sources" if not ranked else ""},
        "state": {"sources_seen": len(sources)},
    }
