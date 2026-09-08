from __future__ import annotations

import json

import pytest

from research_harness.config import SourceSpec
from research_harness.connectors import (
    SourceContractError,
    normalize,
    normalize_book,
    normalize_feed,
    normalize_markets,
)
from research_harness.records import Capture


def capture(context: dict | None = None, status: int = 200) -> Capture:
    return Capture(
        "c",
        "s",
        "https://source.example",
        "2026-09-07T12:00:00Z",
        status,
        "h",
        {},
        context or {"role": "primary"},
    )


def test_closed_active_flag_does_not_mean_tradable(market):
    source = SourceSpec(
        id="m",
        name="Market",
        connector="polymarket",
        url="https://gamma-api.polymarket.com/events?slug=test-agreement",
    )
    market["closed"] = True
    market["acceptingOrders"] = False
    result = normalize_markets(source, [{"id": "event1", "markets": [market]}])
    assert result.records[0].data["status"] == "closed"
    assert result.records[0].data["rules"] == market["description"]
    assert result.records[0].data["closes_at"] == market["endDate"]
    assert result.records[0].data["event_id"] == "event1"


def test_missing_rules_and_misaligned_tokens_are_quarantined(market):
    source = SourceSpec(
        id="m",
        name="Market",
        connector="polymarket",
        url="https://gamma-api.polymarket.com/markets?slug=test-agreement",
    )
    bad = {**market, "description": ""}
    mismatched = {**market, "clobTokenIds": '["111"]'}
    result = normalize_markets(source, [bad, mismatched])
    assert not result.records
    assert len(result.issues) == 2


def test_polymarket_book_sorts_levels_and_validates_token():
    context = {
        "role": "orderbook",
        "venue": "polymarket",
        "market_key": "polymarket:123",
        "outcome": "Yes",
        "token_id": "111",
    }
    data = {
        "asset_id": "111",
        "timestamp": "1788782400000",
        "bids": [{"price": "0.01", "size": "3"}, {"price": "0.07", "size": "8"}],
        "asks": [{"price": "0.90", "size": "500"}, {"price": "0.08", "size": "5"}],
    }
    result = normalize_book(capture(context), data)
    assert result.records[0].data["bids"][0]["price"] == "0.07"
    assert result.records[0].data["asks"][0] == {"price": "0.08", "size": "5"}
    data["asset_id"] = "WRONG"
    assert normalize_book(capture(context), data).issues


@pytest.mark.parametrize(
    "price,size", [("NaN", "5"), ("1.01", "5"), ("-0.1", "5"), ("0.5", "-1"), ("0.5", "0")]
)
def test_invalid_book_numbers_are_quarantined(price, size):
    context = {
        "role": "orderbook",
        "venue": "polymarket",
        "market_key": "polymarket:123",
        "outcome": "Yes",
        "token_id": "111",
    }
    data = {"asset_id": "111", "bids": [], "asks": [{"price": price, "size": size}]}
    result = normalize_book(capture(context), data)
    assert not result.records
    assert len(result.issues) == 1


def test_kalshi_fixed_point_complement_and_empty_books():
    context = {
        "role": "orderbook",
        "venue": "kalshi",
        "market_key": "kalshi:TEST",
        "depth_limit": 100,
    }
    result = normalize_book(
        capture(context),
        {
            "orderbook_fp": {
                "yes_dollars": [["0.1100", "19.53"]],
                "no_dollars": [["0.8500", "103.43"]],
            }
        },
    )
    yes = result.records[0].data
    assert yes["asks"] == [{"price": "0.15", "size": "103.43"}]
    assert yes["bids"] == [{"price": "0.11", "size": "19.53"}]
    empty = normalize_book(
        capture(context), {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
    )
    assert empty.records[0].data["asks"] == []
    unavailable = normalize_book(capture(context, status=404), {})
    assert unavailable.records[0].data["availability"] == "unavailable"
    assert normalize_book(
        capture(context), {"orderbook": {"yes": [[11, 20]], "no": [[85, 10]]}}
    ).issues


def test_crossed_book_is_quarantined():
    context = {"role": "orderbook", "venue": "kalshi", "market_key": "kalshi:TEST"}
    result = normalize_book(
        capture(context),
        {"orderbook_fp": {"yes_dollars": [["0.8", "1"]], "no_dollars": [["0.8", "1"]]}},
    )
    assert result.issues


def test_rss_separates_publication_from_observation_and_excerpt(rss):
    result = normalize_feed(rss, "https://source.example/feed")
    assert result.records[0].published_at == "2026-09-04T12:30:00.000000Z"
    assert result.records[0].data["content_scope"] == "feed_entry"
    assert "official" in result.records[0].data["content"]


def test_atom_namespace_and_relative_link():
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:example:1</id><title>Policy</title><link href="/policy"/><published>2026-09-01T00:00:00+02:00</published><summary>Summary</summary></entry></feed>"""
    result = normalize_feed(xml, "https://source.example/feed")
    assert result.records[0].data["url"] == "https://source.example/policy"
    assert result.records[0].published_at == "2026-08-31T22:00:00.000000Z"


def test_html_or_entity_expansion_cannot_masquerade_as_feed():
    with pytest.raises(SourceContractError):
        normalize_feed(b"<html><body>Sign in</body></html>", "https://source.example")
    with pytest.raises(SourceContractError):
        normalize_feed(
            b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><title>&x;</title></channel></rss>',
            "https://source.example",
        )


def test_200_access_challenge_is_not_a_document():
    source = SourceSpec(id="page", name="Page", connector="html", url="https://source.example")
    with pytest.raises(SourceContractError):
        normalize(
            source,
            capture(),
            b"<html><title>Just a moment...</title><body>Checking your browser</body></html>",
        )


def test_json_missing_required_fields_and_nonfinite_values(source):
    result = normalize(
        source, capture(), json.dumps({"items": [{"id": "1", "published_at": None}]}).encode()
    )
    assert result.issues
    with pytest.raises(SourceContractError):
        normalize(source, capture(), b'{"items":[{"id":"1","title":"bad","value":NaN}]}')


def test_pdf_cannot_pass_an_html_source_probe():
    source = SourceSpec(
        id="paper", name="Paper", connector="html", url="https://source.example/paper.pdf"
    )
    with pytest.raises(SourceContractError):
        normalize(source, capture(), b"%PDF-1.7 binary document")


def test_non_string_publication_timestamp_is_quarantined(source):
    result = normalize(
        source, capture(), b'{"items":[{"id":"1","title":"Policy","published_at":12345}]}'
    )
    assert result.issues
    assert not result.records


def test_html_declared_encoding_is_respected():
    source = SourceSpec(id="page", name="Page", connector="html", url="https://source.example")
    response = Capture(
        "c",
        "s",
        source.url,
        "2026-09-07T12:00:00Z",
        200,
        "h",
        {"content-type": "text/html; charset=iso-8859-1"},
        {"role": "primary"},
    )
    parsed = normalize(
        source,
        response,
        "<html><title>Café</title><body>Décision officielle</body></html>".encode("iso-8859-1"),
    )
    assert parsed.records[0].data["title"] == "Café"
    assert "Décision" in parsed.records[0].data["content"]
