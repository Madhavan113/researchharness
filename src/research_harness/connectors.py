from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from email.message import Message
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from defusedxml import ElementTree

from research_harness.config import SourceSpec
from research_harness.records import (
    Capture,
    Issue,
    Market,
    OrderBook,
    Outcome,
    Parsed,
    Quote,
    Record,
)
from research_harness.util import digest, json_pointer, parse_timestamp, timestamp


class SourceContractError(ValueError):
    pass


def load_json(body: bytes) -> Any:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"Nonfinite JSON constant: {value}")

    try:
        return json.loads(body, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise SourceContractError(f"Invalid JSON response: {exc}") from exc


def array(value: Any, name: str, *, optional: bool = False) -> list[Any]:
    if value is None and optional:
        return []
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise ValueError(f"{name} must be an array")
    return parsed


def published_time(raw: str | None) -> str | None:
    if not raw:
        return None
    if not isinstance(raw, str):
        raise ValueError("A declared publication timestamp must be a string")
    try:
        return timestamp(parse_timestamp(raw))
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(raw)
            return timestamp(parsed) if parsed.tzinfo else None
        except (ValueError, TypeError, OverflowError):
            return None


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.links: list[dict[str, str]] = []
        self.published: str | None = None
        self.has_html_tags = False
        self._skip = 0
        self._title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag in {"html", "body", "head", "p", "article", "main", "section", "div", "title", "h1"}:
            self.has_html_tags = True
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag == "title":
            self._title = True
        if tag in {"link", "a"} and data.get("href"):
            self.links.append(
                {
                    "href": data["href"] or "",
                    "type": data.get("type") or "",
                    "rel": data.get("rel") or "",
                }
            )
        if tag == "meta" and (data.get("property") or data.get("name")) in {
            "article:published_time",
            "date",
            "DC.date.issued",
        }:
            self.published = data.get("content")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._title = False

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title.append(data)
        if not self._skip and data.strip():
            self.parts.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self.parts)


def text_from_html(raw: str) -> str:
    page = PageText()
    page.feed(raw)
    return page.text


def decode_html(body: bytes, content_type: str) -> str:
    message = Message()
    message["content-type"] = content_type
    encoding = message.get_content_charset()
    if not encoding:
        match = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", body[:4096], re.I)
        encoding = match[1].decode("ascii") if match else "utf-8-sig"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeError) as exc:
        raise SourceContractError(
            f"Cannot faithfully decode HTML with declared encoding {encoding}"
        ) from exc


def polymarket_record(item: dict[str, Any], event_id: str | None = None) -> Record:
    labels = array(item.get("outcomes"), "outcomes")
    tokens = array(item.get("clobTokenIds"), "clobTokenIds", optional=True)
    if tokens and len(tokens) != len(labels):
        raise ValueError("Token ids and outcome labels have different lengths")
    if not item.get("id") or not item.get("question") or not item.get("description"):
        raise ValueError("A market requires id, question, and complete rule text")
    flags = {
        key: item.get(key) for key in ["active", "closed", "acceptingOrders", "umaResolutionStatus"]
    }
    if item.get("umaResolutionStatus") == "resolved":
        status = "resolved"
    elif item.get("closed") is True:
        status = "closed"
    elif item.get("active") is True and item.get("acceptingOrders") is True:
        status = "open"
    elif item.get("active") is False:
        status = "inactive"
    else:
        status = "unknown"
    warnings = ["Metadata dates are retained separately from the deadline in the rules."]
    if item.get("active") and item.get("closed"):
        warnings.append("The provider's active flag does not establish tradability.")
    if not item.get("resolutionSource"):
        warnings.append("No structured resolution source; consult the retained rules.")
    market = Market(
        venue="polymarket",
        contract_id=str(item["id"]),
        title=item["question"],
        rules=item["description"],
        event_id=event_id,
        slug=item.get("slug"),
        resolution_source=item.get("resolutionSource") or None,
        status=status,
        provider_status=flags,
        starts_at=item.get("startDate"),
        closes_at=item.get("endDate"),
        outcomes=[
            Outcome(label=label, token_id=str(tokens[index]) if tokens else None)
            for index, label in enumerate(labels)
        ],
        fees_enabled=item.get("feesEnabled"),
        fee_details={
            key: item[key] for key in ["fee", "feeType", "feeSchedule", "feeDetails"] if key in item
        }
        or None,
        warnings=warnings,
    )
    return Record("market", f"polymarket:{market.contract_id}", market.model_dump(mode="json"))


def kalshi_record(item: dict[str, Any]) -> Record:
    if not item.get("ticker") or not item.get("rules_primary"):
        raise ValueError("A Kalshi market requires ticker and rules_primary")
    status = {
        "active": "open",
        "open": "open",
        "closed": "closed",
        "settled": "resolved",
        "finalized": "resolved",
        "initialized": "inactive",
        "unopened": "inactive",
        "inactive": "inactive",
    }.get(item.get("status"), "unknown")
    market = Market(
        venue="kalshi",
        contract_id=item["ticker"],
        title=item.get("title", ""),
        rules="\n\n".join(
            part for part in [item["rules_primary"], item.get("rules_secondary")] if part
        ),
        event_id=item.get("event_ticker"),
        status=status,
        provider_status={"status": item.get("status")},
        starts_at=item.get("open_time"),
        closes_at=item.get("close_time"),
        outcomes=[Outcome(label="Yes"), Outcome(label="No")],
        warnings=[
            "Market-level metadata does not establish fees; retrieve the venue's applicable schedule.",
            "Metadata close time is retained separately from the rule text.",
        ],
    )
    return Record("market", f"kalshi:{market.contract_id}", market.model_dump(mode="json"))


def normalize_markets(source: SourceSpec, payload: Any) -> Parsed:
    result = Parsed()
    if source.connector == "kalshi":
        if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
            raise SourceContractError("Expected a Kalshi object containing markets[]")
        groups = [(None, payload["markets"])]
    else:
        if isinstance(payload, dict) and isinstance(payload.get("markets"), list):
            groups = [(str(payload.get("id")) if payload.get("id") else None, payload["markets"])]
        elif isinstance(payload, dict) and "question" in payload:
            groups = [(None, [payload])]
        elif isinstance(payload, list):
            groups = []
            for item in payload:
                if isinstance(item, dict) and isinstance(item.get("markets"), list):
                    groups.append(
                        (str(item.get("id")) if item.get("id") else None, item["markets"])
                    )
                else:
                    groups.append((None, [item]))
        else:
            raise SourceContractError("Expected a Polymarket market list or event with markets[]")
    for event_id, items in groups:
        for index, item in enumerate(items):
            try:
                if not isinstance(item, dict):
                    raise ValueError("Market entry must be an object")
                result.records.append(
                    kalshi_record(item)
                    if source.connector == "kalshi"
                    else polymarket_record(item, event_id)
                )
            except (ValueError, TypeError, KeyError) as exc:
                result.issues.append(Issue(f"event={event_id};item={index}", str(exc), item))
    return result


def normalize_book(capture: Capture, payload: Any) -> Parsed:
    context = capture.context
    result = Parsed()
    try:
        if capture.status_code == 404:
            labels = [context["outcome"]] if context["venue"] == "polymarket" else ["Yes", "No"]
            for label in labels:
                book = OrderBook(
                    venue=context["venue"],
                    market_key=context["market_key"],
                    outcome=label,
                    token_id=context.get("token_id"),
                    availability="unavailable",
                    bids=[],
                    asks=[],
                    warnings=["Venue returned 404; no executable price is inferred."],
                )
                result.records.append(
                    Record("orderbook", f"{book.market_key}:{label}", book.model_dump(mode="json"))
                )
            return result
        if not isinstance(payload, dict):
            raise ValueError("Order book must be an object")
        if context["venue"] == "polymarket":
            if not isinstance(payload.get("bids"), list) or not isinstance(
                payload.get("asks"), list
            ):
                raise ValueError("Missing bids or asks arrays")
            if str(payload.get("asset_id")) != context["token_id"]:
                raise ValueError("Order-book token does not match the requested outcome")
            venue_time = None
            if payload.get("timestamp"):
                venue_time = timestamp(
                    datetime.fromtimestamp(int(payload["timestamp"]) / 1000, UTC)
                )
            book = OrderBook(
                venue="polymarket",
                market_key=context["market_key"],
                outcome=context["outcome"],
                token_id=context["token_id"],
                venue_timestamp=venue_time,
                bids=[Quote(**level) for level in payload["bids"]],
                asks=[Quote(**level) for level in payload["asks"]],
            )
            result.records.append(
                Record(
                    "orderbook", f"{book.market_key}:{book.outcome}", book.model_dump(mode="json")
                )
            )
        else:
            values = payload.get("orderbook_fp")
            if (
                not isinstance(values, dict)
                or "yes_dollars" not in values
                or "no_dollars" not in values
            ):
                raise ValueError(
                    "Expected orderbook_fp with yes_dollars and no_dollars; legacy units are not guessed"
                )
            for label, own, other in [
                ("Yes", "yes_dollars", "no_dollars"),
                ("No", "no_dollars", "yes_dollars"),
            ]:
                bids = [Quote(price=level[0], size=level[1]) for level in values[own]]
                opposite = [Quote(price=level[0], size=level[1]) for level in values[other]]
                asks = [
                    Quote(price=str(Decimal(1) - Decimal(level.price)), size=level.size)
                    for level in opposite
                ]
                book = OrderBook(
                    venue="kalshi",
                    market_key=context["market_key"],
                    outcome=label,
                    bids=bids,
                    asks=asks,
                    depth_limit=context.get("depth_limit"),
                    warnings=[
                        "Asks derived from complementary outcome bids; quantities are contracts."
                    ],
                )
                result.records.append(
                    Record("orderbook", f"{book.market_key}:{label}", book.model_dump(mode="json"))
                )
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        return Parsed(issues=[Issue("orderbook", str(exc), payload)])
    return result


def _element_text(element: Any, names: list[str]) -> str | None:
    for name in names:
        child = element.find(name)
        if child is not None:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return None


def normalize_feed(body: bytes, url: str) -> Parsed:
    try:
        root = ElementTree.fromstring(body)
    except Exception as exc:
        raise SourceContractError(
            f"Invalid or unsafe feed XML: {type(exc).__name__}: {exc}"
        ) from exc
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == "rss":
        entries = root.findall("./channel/item")
    elif root.tag == atom + "feed":
        entries = root.findall(atom + "entry")
    else:
        raise SourceContractError("Response is not RSS 2.0 or Atom")
    result = Parsed()
    for index, entry in enumerate(entries):
        try:
            title = _element_text(entry, ["title", atom + "title"])
            link = _element_text(entry, ["link"])
            if not link:
                for node in entry.findall(atom + "link"):
                    if node.get("rel", "alternate") == "alternate" and node.get("href"):
                        link = node.get("href")
                        break
            link = urljoin(url, link) if link else None
            identity = _element_text(entry, ["guid", atom + "id"]) or link
            if not title or not identity:
                raise ValueError("Feed entry needs a title and a stable guid/id or link")
            content = (
                _element_text(
                    entry,
                    [
                        "{http://purl.org/rss/1.0/modules/content/}encoded",
                        "description",
                        atom + "content",
                        atom + "summary",
                    ],
                )
                or ""
            )
            raw_date = _element_text(
                entry, ["pubDate", atom + "published", "{http://purl.org/dc/elements/1.1/}date"]
            )
            updated = _element_text(entry, [atom + "updated"])
            result.records.append(
                Record(
                    "document",
                    digest(identity),
                    {
                        "title": title,
                        "url": link,
                        "external_id": identity,
                        "content": text_from_html(content),
                        "content_scope": "feed_entry",
                        "published_value": raw_date,
                        "updated_value": updated,
                        "warnings": [
                            "Feed text may be an excerpt; full article retrieval is a separate source."
                        ],
                    },
                    published_time(raw_date),
                )
            )
        except (ValueError, TypeError) as exc:
            result.issues.append(Issue(f"entry={index}", str(exc)))
    return result


def normalize(source: SourceSpec, capture: Capture, body: bytes) -> Parsed:
    if capture.context.get("role") == "orderbook":
        return normalize_book(capture, {} if capture.status_code == 404 else load_json(body))
    if source.connector in {"polymarket", "kalshi"}:
        return normalize_markets(source, load_json(body))
    if source.connector == "rss":
        return normalize_feed(body, capture.url)
    if source.connector == "html":
        content_type = capture.headers.get("content-type", "").split(";", 1)[0].lower().strip()
        if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
            raise SourceContractError(
                f"HTML connector received unsupported content type: {content_type}"
            )
        page = PageText()
        page.feed(decode_html(body, capture.headers.get("content-type", "")))
        title = " ".join(page.title).strip()
        if (
            not page.has_html_tags
            or not page.text
            or title.lower().strip()
            in {
                "just a moment...",
                "access denied",
                "sign in",
                "log in",
                "forbidden",
            }
        ):
            raise SourceContractError(
                "Page has no usable text or appears to be an access challenge"
            )
        return Parsed(
            records=[
                Record(
                    "document",
                    digest(source.url),
                    {
                        "title": title or source.name,
                        "url": capture.url,
                        "content": page.text,
                        "content_scope": "page_text",
                        "published_value": page.published,
                        "warnings": [
                            "HTML text extraction retains page navigation; semantic article extraction is not inferred."
                        ],
                    },
                    published_time(page.published),
                )
            ]
        )
    payload = load_json(body)
    try:
        items = json_pointer(payload, source.items_pointer)
    except (KeyError, ValueError, IndexError) as exc:
        raise SourceContractError(f"Missing items pointer {source.items_pointer!r}") from exc
    if not isinstance(items, list):
        raise SourceContractError("JSON items_pointer must select an array")
    result = Parsed()
    for index, item in enumerate(items):
        try:
            if not isinstance(item, dict):
                raise ValueError("JSON record must be an object")
            key = json_pointer(item, source.id_pointer or "")
            if isinstance(key, bool) or not isinstance(key, (str, int)) or str(key) == "":
                raise ValueError("Record id must be a nonempty string or integer")
            for pointer in source.required_pointers:
                if json_pointer(item, pointer) is None:
                    raise ValueError(f"Required value is null: {pointer}")
            raw_date = (
                json_pointer(item, source.published_pointer) if source.published_pointer else None
            )
            result.records.append(Record("observation", str(key), item, published_time(raw_date)))
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            result.issues.append(Issue(f"item={index}", str(exc), item))
    return result


CONNECTOR_CATALOG = {
    "polymarket": "Official Gamma market arrays or event arrays with nested markets. Preserve rule text, status flags, outcome ids; optionally collect live CLOB books.",
    "kalshi": "Official /markets response with cursor pagination. Dollar/fixed-point books; optional order-book collection for open markets. Historical archive is separate.",
    "rss": "RSS 2.0 or Atom feed entries. Stable GUID/link, title, feed excerpt and declared publication time; does not follow article links.",
    "json": "Public JSON array selected by items_pointer; stable id_pointer, optional published_pointer, required_pointers; explicit cursor or next-URL pagination.",
    "html": "A public server-rendered HTML page, saved with its extracted text. No JavaScript execution, PDF parsing or semantic field invention.",
}
