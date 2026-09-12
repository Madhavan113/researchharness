"""Rebuild the authored development fixtures; these are not reviewed real-world data."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent
ENTRIES = []


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    path.write_bytes(raw)
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(raw).hexdigest()}


def source(identity, url, **fields):
    return {
        "id": identity,
        "name": identity.replace("-", " ").title(),
        "connector": "json",
        "url": url,
        "items_pointer": "/items",
        "id_pointer": "/id",
        **fields,
    }


def response(url, items=None, *, body=None, content_type="application/json", status=200):
    if isinstance(body, bytes):
        return {
            "url": url,
            "status": status,
            "headers": {"content-type": content_type},
            "body_base64": base64.b64encode(body).decode("ascii"),
        }
    return {
        "url": url,
        "status": status,
        "headers": {"content-type": content_type},
        "body": body
        if body is not None
        else {
            "synthetic": True,
            "items": items
            or [
                {
                    "id": "notice-1",
                    "title": "Authored policy notice",
                    "published_at": "2026-09-01T12:00:00Z",
                    "downloaded_at": "2026-09-08T12:00:00Z",
                }
            ],
        },
    }


def requirement(identity, *alternatives, weight=1, gaps=None):
    return {
        "id": identity,
        "weight": weight,
        "any_of": list(alternatives),
        **({"gap_any_of": gaps} if gaps else {}),
    }


def gap(url, status, status_code, content_type=None):
    return {
        "url": url,
        "status": status,
        "status_code": status_code,
        **({"content_type": content_type} if content_type else {}),
    }


def workbook(url):
    """A deterministic, actual one-sheet XLSX document with monthly rate cells."""
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Monthly rates" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Month</t></is></c><c r="B1" t="inlineStr"><is><t>Rate</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>2020-01</t></is></c><c r="B2"><v>1.5</v></c></row></sheetData></worksheet>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return response(
        url,
        body=output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def vote_pdf(url):
    """A small PDF with a page, text stream, font, and exact cross-reference offsets."""
    text = b"BT /F1 12 Tf 72 720 Td (Authored roll call: Bill 1; Ada Yea; Bea Nay.) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for index, value in enumerate(objects, 1):
        offsets.append(len(body))
        body += f"{index} 0 obj\n".encode() + value + b"\nendobj\n"
    start = len(body)
    body += b"xref\n0 6\n0000000000 65535 f \n"
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    body += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return response(url, body=body, content_type="application/pdf")


def predicate(value, **extra):
    return {"url": value["url"], "connector": value["connector"], **extra}


def rss(url, *, title="Authored notice"):
    return response(
        url,
        content_type="application/rss+xml",
        body=f'<rss version="2.0"><channel><title>Authored fixture feed</title><description>Synthetic development data</description><item><guid>fixture-1</guid><title>{title}</title><link>{url}/notice-1</link><pubDate>Tue, 01 Sep 2026 12:00:00 GMT</pubDate><description>Illustrative source content only.</description></item></channel></rss>',
    )


def case(
    identity,
    title,
    brief,
    topic,
    families,
    tags,
    sources,
    responses,
    requirements,
    *,
    chosen=None,
    unsupported=None,
    manual=None,
):
    unsupported = unsupported or []
    result_urls = list(
        dict.fromkeys([value["url"] for value in sources] + [value["url"] for value in unsupported])
    )
    # This parseable near-topic result is deliberately outside the source answer set.
    # It provides publication counts, not the underlying records requested by the brief.
    index_url = urljoin(result_urls[0], "/publication-index.json")
    assert index_url not in result_urls
    responses = [
        *responses,
        response(index_url, [{"id": "monthly-index", "topic": title, "publication_count": 12}]),
    ]
    fixture = write(
        ROOT / "fixtures" / f"{identity}.json",
        {
            "authorship": "Synthetic fixture authored by Codex; no live source was sampled",
            "search_results": [
                {"url": index_url, "title": f"{title}: publication index and monthly counts"},
                *[{"url": url, "title": f"{title}: source publication"} for url in result_urls],
            ],
            "responses": responses,
        },
    )
    value = {
        "id": identity,
        "title": title,
        "brief": brief,
        "topic_group": topic,
        "source_families": families,
        "tags": tags,
        "review_status": "authored",
        "review_notes": "Authored development contract, not independently human-reviewed. Endpoints, records, and search ordering are synthetic.",
        "specification": {"requirements": requirements},
        "fixtures": fixture,
        "sources": sources,
        "fixture_plan": {
            "source_ids": chosen if chosen is not None else [sources[-1]["id"]] if sources else [],
            "unsupported": unsupported,
        },
        "manual_checks": manual
        or [
            "Review relevance and whether the source scope answers the full brief.",
            "Confirm the proposal states sample, history, and freshness limitations without inventing access.",
        ],
    }
    ENTRIES.append(write(ROOT / "cases" / f"{identity}.json", value))


def main():
    ENTRIES.clear()
    url = "https://export-office.fixture.example/notices.json"
    good = source(
        "official-notices", url, published_pointer="/published_at", required_pointers=["/title"]
    )
    case(
        "export-notices",
        "Export control notices",
        "Find a public structured source for newly issued export-control notices, preserving notice identity and publication time rather than download time.",
        "export-controls",
        ["export-office"],
        ["official-source", "timestamps"],
        [
            source(
                "notice-download-date",
                url,
                published_pointer="/downloaded_at",
                required_pointers=["/title"],
            ),
            good,
        ],
        [response(url)],
        [
            requirement(
                "official-notices",
                predicate(good, published_pointer="/published_at", required_pointers=["/title"]),
            )
        ],
    )

    url = "https://export-office.fixture.example/license-history.json"
    current = source(
        "current-license-summary",
        "https://export-office.fixture.example/current-license-summary.json",
    )
    case(
        "license-history-access",
        "Historical export licenses",
        "Find monthly export-license decisions from 2018 onward. If the archive requires credentials, preserve the gap and explain the access requirement rather than presenting current notices as history.",
        "export-controls",
        ["export-office"],
        ["unsupported", "access", "historical-gap"],
        [current],
        [
            response(current["url"], [{"id": "summary-2026", "year": 2026, "approved_count": 14}]),
            response(url, body={"error": "Licensed archive"}, status=401),
        ],
        [
            requirement(
                "license-history",
                gaps=[gap(url, "needs_access", 401)],
            )
        ],
        chosen=[],
        unsupported=[
            {
                "name": "License decision archive",
                "url": url,
                "status": "needs_access",
                "limitation": "The authored archive requires credentials; historical coverage has not been established.",
            }
        ],
    )

    url = "https://tariff-office.fixture.example/bulletins"
    incomplete = source("first-page-only", url)
    complete = source(
        "paginated-bulletins",
        url,
        pagination={"mode": "cursor", "next_pointer": "/next_cursor", "cursor_parameter": "cursor"},
    )
    case(
        "tariff-pagination",
        "Tariff bulletin enumeration",
        "Collect every tariff bulletin, including continuation pages; a compatible first page is not a complete bulletin catalog.",
        "tariffs",
        ["tariff-office"],
        ["pagination", "parseable-incomplete"],
        [incomplete, complete],
        [
            response(url, body={"items": [{"id": "t-1"}], "next_cursor": "second"}),
            response(url + "?cursor=second", body={"items": [{"id": "t-2"}], "next_cursor": None}),
        ],
        [
            requirement(
                "all-bulletins",
                predicate(
                    complete,
                    pagination={
                        "mode": "cursor",
                        "next_pointer": "/next_cursor",
                        "cursor_parameter": "cursor",
                    },
                ),
            )
        ],
        manual=[
            "Run full collection and verify both pages publish atomically; the source score checks configuration only."
        ],
    )

    wrong = source("port-holidays", "https://customs-office.fixture.example/port-holidays.json")
    good = source("customs-rulings", "https://customs-office.fixture.example/rulings.json")
    case(
        "customs-relevance",
        "Customs classification rulings",
        "Monitor customs classification rulings. Do not use port holiday calendars simply because they are official, public, and parseable.",
        "customs-classification",
        ["customs-office"],
        ["irrelevant-parseable", "relevance"],
        [wrong, good],
        [
            response(wrong["url"], [{"id": "holiday-1", "title": "Port holiday"}]),
            response(good["url"], [{"id": "ruling-1", "title": "Classification ruling"}]),
        ],
        [requirement("rulings", predicate(good))],
    )

    url = "https://sanctions-office.fixture.example/updates.xml"
    good = {
        "id": "sanctions-updates",
        "name": "Sanctions update feed",
        "connector": "rss",
        "url": url,
    }
    archive = "https://sanctions-office.fixture.example/historical-designations.json"
    case(
        "sanctions-current-history",
        "Sanctions changes and history",
        "Track new sanctions changes and reconstruct the designated-party list as it stood each month in 2020. Keep current update coverage separate from unavailable point-in-time history.",
        "sanctions",
        ["sanctions-office"],
        ["rss", "historical-gap", "partial-coverage"],
        [good],
        [
            rss(url),
            response(
                archive,
                body={"error": "Historical snapshots require archive credentials"},
                status=403,
            ),
        ],
        [
            requirement("current-updates", predicate(good)),
            requirement(
                "historical-designations",
                weight=2,
                gaps=[gap(archive, "needs_access", 403)],
            ),
        ],
        unsupported=[
            {
                "name": "Historical designation snapshots",
                "url": archive,
                "status": "needs_access",
                "limitation": "Current RSS entries do not reconstruct old point-in-time designation lists.",
            }
        ],
    )

    url = "https://central-bank.fixture.example/releases.json"
    wrong = source("download-date", url, published_pointer="/downloaded_at")
    good = source("release-date", url, published_pointer="/published_at")
    case(
        "central-bank-publication-time",
        "Central bank release timing",
        "Collect policy release timestamps for point-in-time analysis. Preserve the institution's release time; today's download timestamp must not become historical publication time.",
        "monetary-policy",
        ["central-bank"],
        ["timestamps", "parseable-wrong-semantics"],
        [wrong, good],
        [response(url)],
        [requirement("policy-releases", predicate(good, published_pointer="/published_at"))],
    )

    url = "https://central-bank.fixture.example/rates.xlsx"
    current = source(
        "current-policy-rate", "https://central-bank.fixture.example/current-rate.json"
    )
    case(
        "rates-workbook-unsupported",
        "Historical rate workbook",
        "Find the central bank's historical rate workbook and describe the connector needed to ingest its monthly worksheets. Do not claim that a downloadable spreadsheet is a supported JSON feed.",
        "monetary-policy",
        ["central-bank"],
        ["unsupported", "spreadsheet"],
        [current],
        [response(current["url"], [{"id": "current-rate", "rate": 2.5}]), workbook(url)],
        [
            requirement(
                "monthly-rates",
                gaps=[
                    gap(
                        url,
                        "needs_connector",
                        200,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                ],
            )
        ],
        chosen=[],
        unsupported=[
            {
                "name": "Monthly rate workbook",
                "url": url,
                "status": "needs_connector",
                "limitation": "The response is a workbook; the current harness has no worksheet extraction connector. Current-rate JSON does not supply monthly history.",
            }
        ],
    )

    url = "https://procurement-office.fixture.example/notices.json"
    wrong = source("title-as-id", url, id_pointer="/title")
    good = source("stable-notice-id", url, id_pointer="/notice_id")
    case(
        "procurement-stable-identity",
        "Procurement notice revisions",
        "Track revisions to procurement notices. Use a stable notice identifier so a title correction updates an existing notice rather than creating a different record.",
        "procurement",
        ["procurement-office"],
        ["stable-identity", "revisions"],
        [wrong, good],
        [response(url, [{"notice_id": "P-2026-1", "title": "Bridge materials"}])],
        [requirement("stable-notices", predicate(good, id_pointer="/notice_id"))],
    )

    url = "https://tender-service.fixture.example/awards"
    wrong = source(
        "missing-terminal-rule",
        url,
        pagination={"mode": "cursor", "next_pointer": "/next", "cursor_parameter": "after"},
    )
    good = source(
        "documented-terminal-rule",
        url,
        pagination={
            "mode": "cursor",
            "next_pointer": "/next",
            "cursor_parameter": "after",
            "stop_when_missing": True,
        },
    )
    case(
        "award-terminal-pagination",
        "Award catalog terminal page",
        "Enumerate contract awards from an API whose documented last page omits the continuation field. Configure that terminal-page convention explicitly and verify full collection later.",
        "procurement",
        ["tender-service"],
        ["pagination", "missing-terminal-cursor"],
        [wrong, good],
        [response(url)],
        [
            requirement(
                "awards", predicate(good, pagination={"stop_when_missing": True, "mode": "cursor"})
            )
        ],
        manual=[
            "Confirm missing continuation is documented as terminal, not a transient malformed response.",
            "Run complete collection; the sample-probe score does not validate termination.",
        ],
    )

    url = "https://cyber-office.fixture.example/advisories.json"
    wrong = source("modified-as-published", url, published_pointer="/lastModified")
    good = source("original-publication", url, published_pointer="/datePublished")
    case(
        "cyber-advisory-dates",
        "Advisory release versus amendment",
        "Monitor cybersecurity advisories while distinguishing the original publication date from later amendments. Preserve identity across updates.",
        "cyber-advisories",
        ["cyber-office"],
        ["timestamps", "revisions"],
        [wrong, good],
        [
            response(
                url,
                [
                    {
                        "id": "ADV-1",
                        "datePublished": "2026-09-01T08:00:00Z",
                        "lastModified": "2026-09-07T08:00:00Z",
                    }
                ],
            )
        ],
        [requirement("original-release-date", predicate(good, published_pointer="/datePublished"))],
    )

    first = source("vendor-advisories", "https://vendor-security.fixture.example/advisories.json")
    counts = source(
        "vendor-advisory-counts", "https://vendor-security.fixture.example/advisory-counts.json"
    )
    alternative = {
        "id": "vendor-feed",
        "name": "Official security RSS",
        "connector": "rss",
        "url": "https://vendor-security.fixture.example/security.xml",
    }
    case(
        "vendor-valid-alternatives",
        "Official vendor advisory alternatives",
        "Find an official source for vendor security advisories. Either the public structured advisory endpoint or the official RSS feed is acceptable when limitations are stated.",
        "vendor-security",
        ["vendor-security"],
        ["valid-alternatives", "rss"],
        [counts, first, alternative],
        [
            response(counts["url"], [{"id": "2026-09", "advisory_count": 4}]),
            response(first["url"]),
            rss(alternative["url"]),
        ],
        [requirement("vendor-advisories", predicate(first), predicate(alternative))],
    )

    wrong = source("press-office-stats", "https://energy-agency.fixture.example/press-stats.json")
    good = source(
        "weekly-storage",
        "https://energy-agency.fixture.example/storage.json",
        required_pointers=["/week_start", "/storage"],
    )
    case(
        "energy-series-relevance",
        "Weekly energy storage series",
        "Find weekly energy storage observations with the reporting week and storage value. An agency press-release count is not a substitute for the requested series.",
        "energy-storage",
        ["energy-agency"],
        ["irrelevant-parseable", "required-fields"],
        [wrong, good],
        [
            response(wrong["url"], [{"id": "press-1", "count": 2}]),
            response(good["url"], [{"id": "week-1", "week_start": "2026-08-31", "storage": 321}]),
        ],
        [
            requirement(
                "weekly-storage", predicate(good, required_pointers=["/week_start", "/storage"])
            )
        ],
    )

    url = "https://grid-operator.fixture.example/outages"
    wrong = source("outages-page-one", url)
    good = source("outages-next-url", url, pagination={"mode": "next_url", "next_pointer": "/next"})
    case(
        "grid-next-url-pagination",
        "Outage event pagination",
        "Collect all active outage events from an endpoint that provides an absolute next-page URL. Retain outage identity and explicit continuation behavior.",
        "grid-outages",
        ["grid-operator"],
        ["pagination", "next-url"],
        [wrong, good],
        [
            response(url, body={"items": [{"id": "outage-1"}], "next": url + "/page-2"}),
            response(url + "/page-2", body={"items": [{"id": "outage-2"}], "next": None}),
        ],
        [
            requirement(
                "outage-catalog",
                predicate(good, pagination={"mode": "next_url", "next_pointer": "/next"}),
            )
        ],
    )

    url = "https://port-authority.fixture.example/arrivals"
    pagination = {"mode": "cursor", "next_pointer": "/next", "cursor_parameter": "cursor"}
    wrong = source("single-page-budget", url, pagination=pagination, max_pages=1)
    good = source("three-page-budget", url, pagination=pagination, max_pages=3)
    case(
        "port-page-budget",
        "Port arrival catalog bounds",
        "Collect the three-page arrival catalog within a declared request budget. A one-page cap must not be described as full enumeration.",
        "port-arrivals",
        ["port-authority"],
        ["pagination", "page-budget"],
        [wrong, good],
        [
            response(url, body={"items": [{"id": "arrival-1"}], "next": "two"}),
            response(url + "?cursor=two", body={"items": [{"id": "arrival-2"}], "next": "three"}),
            response(url + "?cursor=three", body={"items": [{"id": "arrival-3"}], "next": None}),
        ],
        [requirement("arrival-catalog", predicate(good, max_pages=3, pagination=pagination))],
        manual=[
            "Verify all three pages during ingestion and reject partial publication when the page limit is exhausted."
        ],
    )

    url = "https://weather-office.fixture.example/alerts.xml"
    good = {"id": "official-alerts", "name": "Weather alert feed", "connector": "rss", "url": url}
    summary = {
        "id": "weekly-weather",
        "name": "Weekly weather summary",
        "connector": "rss",
        "url": "https://weather-office.fixture.example/weekly.xml",
    }
    case(
        "weather-alert-feed",
        "Official weather alerts",
        "Monitor the official weather alert feed and retain stable alert ids and publication dates. State that feed retention does not establish a historical alert archive.",
        "weather-alerts",
        ["weather-office"],
        ["rss", "freshness-limit"],
        [summary, good],
        [
            rss(summary["url"], title="Weekly rainfall summary"),
            rss(url, title="Authored flood advisory"),
        ],
        [requirement("weather-alerts", predicate(good))],
    )

    wrong = source("retailer-promotions", "https://recall-catalog.fixture.example/promotions.json")
    good = {
        "id": "official-recalls",
        "name": "Product recall feed",
        "connector": "rss",
        "url": "https://recall-office.fixture.example/recalls.xml",
    }
    case(
        "recall-source-relevance",
        "Official product recalls",
        "Find official product recall notices. Do not confuse a parseable retailer promotions endpoint with evidence that a product was recalled.",
        "product-recalls",
        ["recall-catalog", "recall-office"],
        ["irrelevant-parseable", "rss"],
        [wrong, good],
        [
            response(wrong["url"], [{"id": "sale-1", "title": "Promotion"}]),
            rss(good["url"], title="Authored recall notice"),
        ],
        [requirement("official-recalls", predicate(good))],
    )

    url = "https://issuer-news.fixture.example/earnings.json"
    wrong = source("retrieval-timestamp", url, published_pointer="/downloaded_at")
    good = source("release-timestamp", url, published_pointer="/published_at")
    case(
        "issuer-release-timestamp",
        "Issuer earnings release timing",
        "Collect earnings release text with the original timezone-aware publication timestamp for later point-in-time analysis. Do not infer historical availability from retrieval time.",
        "issuer-releases",
        ["issuer-news"],
        ["timestamps", "point-in-time"],
        [wrong, good],
        [response(url)],
        [requirement("issuer-releases", predicate(good, published_pointer="/published_at"))],
    )

    url = "https://filings-registry.fixture.example/index.json"
    wrong = source("row-number-id", url, id_pointer="/row")
    good = source(
        "accession-id", url, id_pointer="/accession", required_pointers=["/form", "/filed_at"]
    )
    case(
        "filing-stable-accession",
        "Stable filing identities",
        "Track filings using stable accession ids, form type, and filed date. Row numbers in a reordered index must not become filing identity.",
        "company-filings",
        ["filings-registry"],
        ["stable-identity", "required-fields"],
        [wrong, good],
        [
            response(
                url,
                [
                    {
                        "row": "1",
                        "accession": "000-fixture-2026",
                        "form": "ANNUAL",
                        "filed_at": "2026-09-01T10:00:00Z",
                    }
                ],
            )
        ],
        [
            requirement(
                "stable-filings",
                predicate(good, id_pointer="/accession", required_pointers=["/form", "/filed_at"]),
            )
        ],
    )

    catalog = source(
        "contract-catalog", "https://prediction-exchange.fixture.example/contracts.json"
    )
    rules = source(
        "contract-rules",
        "https://prediction-exchange.fixture.example/rules.json",
        required_pointers=["/settlement_text"],
    )
    case(
        "market-contract-and-rules",
        "Contract metadata and settlement text",
        "Collect prediction-market contract metadata plus authoritative settlement wording. A market title or price series alone does not establish the resolution criteria. Do not generate trading advice.",
        "prediction-contracts",
        ["prediction-exchange"],
        ["multiple-needs", "settlement-evidence"],
        [catalog, rules],
        [
            response(
                catalog["url"],
                [{"id": "contract-1", "title": "Authored trade announcement question"}],
            ),
            response(
                rules["url"],
                [
                    {
                        "id": "contract-1",
                        "settlement_text": "Illustrative official release criterion",
                    }
                ],
            ),
        ],
        [
            requirement("contract-metadata", predicate(catalog)),
            requirement(
                "settlement-wording",
                predicate(rules, required_pointers=["/settlement_text"]),
                weight=2,
            ),
        ],
        chosen=[catalog["id"], rules["id"]],
        manual=[
            "Read the source wording and verify the proposed join preserves the correct contract's criteria."
        ],
    )

    good = source("bill-status", "https://legislature.fixture.example/bills.json")
    votes = "https://legislature.fixture.example/roll-call.pdf"
    case(
        "legislation-unsupported-votes",
        "Bill status and roll-call evidence",
        "Track bill status updates and named roll-call votes. The status API is supported; the authored vote document is a PDF requiring a separate extraction connector. Preserve that unresolved need.",
        "legislation",
        ["legislature"],
        ["unsupported", "pdf", "partial-coverage"],
        [good],
        [response(good["url"], [{"id": "bill-1", "status": "committee"}]), vote_pdf(votes)],
        [
            requirement("bill-status", predicate(good)),
            requirement(
                "roll-call-votes", gaps=[gap(votes, "needs_connector", 200, "application/pdf")]
            ),
        ],
        unsupported=[
            {
                "name": "Roll-call PDF",
                "url": votes,
                "status": "needs_connector",
                "limitation": "PDF table extraction is unsupported; a bill status does not identify individual votes.",
            }
        ],
    )

    assert len(ENTRIES) == 20
    write(
        ROOT / "manifest.json",
        {
            "schema_version": 1,
            "id": "research-discovery-development-v2",
            "split": "development",
            "description": "Twenty authored synthetic development contracts. No independent human review or live model quality claims.",
            "cases": ENTRIES,
        },
    )


if __name__ == "__main__":
    main()
